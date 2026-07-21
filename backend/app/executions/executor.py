from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, cast
from uuid import UUID

from app.executions.csv_versioning import CsvMutationError, TargetVersionLike
from app.executions.verifier import TargetVerifier
from app.governance.dependency_graph import stable_topological_order
from app.schemas.executions import (
    ExecutionBatchResult,
    ExecutionBatchStatus,
    ExecutionOperationResult,
    GovernanceOperation,
    OperationStatus,
    OperationType,
)


class ConnectorExecutionError(RuntimeError):
    code = "connector_error"
    retryable = False


class RetryableConnectorError(ConnectorExecutionError):
    code = "connector_retryable"
    retryable = True


@dataclass(frozen=True)
class StoredExecutionOperation:
    record_id: UUID
    operation: GovernanceOperation

    @property
    def id(self) -> UUID:
        return self.operation.id

    @property
    def dependencies(self) -> frozenset[UUID]:
        return self.operation.dependencies


class AttemptLike(Protocol):
    attempt_number: int
    status: str
    error_code: str | None
    retryable: bool
    actual_after: dict[str, Any] | None
    verification: dict[str, Any] | None


class ExecutionRepositoryPort(Protocol):
    async def get_batch(self, batch_id: UUID) -> Any: ...

    async def get_target_version(self, version_id: UUID) -> TargetVersionLike | None: ...

    async def retry_target_version(self, batch_id: UUID) -> TargetVersionLike: ...

    async def execution_operations(
        self, batch_id: UUID
    ) -> tuple[StoredExecutionOperation, ...]: ...

    async def list_attempts(self, operation_id: UUID) -> tuple[AttemptLike, ...]: ...

    async def append_attempt(
        self,
        operation_id: UUID,
        *,
        status: str | OperationStatus,
        error_code: str | None = None,
        error_detail: Mapping[str, Any] | None = None,
        actual_after: Mapping[str, Any] | None = None,
        verification: Mapping[str, Any] | None = None,
        retryable: bool = False,
        target_version_id: UUID | None = None,
    ) -> AttemptLike: ...

    async def append_audit_event(
        self,
        *,
        batch_id: UUID,
        actor_id: str,
        event_type: str,
        details: Mapping[str, Any],
        operation_id: UUID | None = None,
    ) -> Any: ...


class TargetMutationSession(Protocol):
    async def apply_operation(self, operation: GovernanceOperation) -> None: ...

    async def read_entity(self, identifier: str) -> dict[str, object] | None: ...

    async def finalize(self) -> Any: ...

    async def abort(self) -> None: ...


class ExecutableTarget(Protocol):
    async def begin(
        self,
        parent: TargetVersionLike,
        *,
        batch_id: UUID,
    ) -> TargetMutationSession: ...


class ExecutionExecutor:
    def __init__(
        self,
        *,
        repository: ExecutionRepositoryPort,
        target: ExecutableTarget,
        verifier: TargetVerifier,
    ) -> None:
        self.repository = repository
        self.target = target
        self.verifier = verifier

    async def execute(
        self,
        batch_id: UUID,
        *,
        retry_only: frozenset[UUID] | None = None,
    ) -> ExecutionBatchResult:
        batch = await self.repository.get_batch(batch_id)
        if batch is None:
            raise LookupError("execution batch not found")
        parent = await self.repository.get_target_version(batch.input_target_version_id)
        if parent is None:
            raise LookupError("execution target version not found")
        if retry_only is not None:
            parent = await self.repository.retry_target_version(batch_id)
        stored = await self.repository.execution_operations(batch_id)
        by_operation_id = {item.operation.id: item for item in stored}
        ordered = stable_topological_order(stored)
        session = await self.target.begin(parent, batch_id=batch_id)
        results: dict[UUID, ExecutionOperationResult] = {}
        pending_attempts: list[tuple[StoredExecutionOperation, dict[str, Any]]] = []
        applied = False
        try:
            for item in ordered:
                if retry_only is not None and not {
                    item.record_id,
                    item.operation.id,
                }.intersection(retry_only):
                    continue
                prior = await self.repository.list_attempts(item.record_id)
                latest = prior[-1] if prior else None
                if latest is not None and latest.status == OperationStatus.SUCCEEDED.value:
                    results[item.record_id] = _result(item, latest)
                    continue
                if retry_only is not None and (
                    latest is None
                    or latest.status != OperationStatus.FAILED.value
                    or not latest.retryable
                ):
                    raise ValueError("operation is not eligible for retry")
                failed_dependency = await self._failed_dependency(
                    item.operation,
                    by_operation_id,
                    results,
                )
                if failed_dependency:
                    values = {
                        "status": OperationStatus.BLOCKED.value,
                        "error_code": "dependency_failed",
                        "error_detail": {"dependency_operation_id": str(failed_dependency)},
                        "retryable": False,
                    }
                    pending_attempts.append((item, values))
                    results[item.record_id] = _pending_result(item, latest, values)
                    continue
                try:
                    if item.operation.operation_type is not OperationType.SKIP:
                        await session.apply_operation(item.operation)
                        applied = True
                    verification = await self.verifier.verify(session, item.operation)
                    status = (
                        OperationStatus.SUCCEEDED
                        if verification.valid
                        else OperationStatus.VERIFICATION_FAILED
                    )
                    values = {
                        "status": status.value,
                        "actual_after": verification.actual,
                        "verification": verification.model_dump(mode="json"),
                        "retryable": False,
                    }
                except (ConnectorExecutionError, CsvMutationError) as error:
                    retryable = bool(getattr(error, "retryable", False))
                    values = {
                        "status": OperationStatus.FAILED.value,
                        "error_code": str(getattr(error, "code", "csv_mutation_error")),
                        "error_detail": {"message": str(error)},
                        "retryable": retryable,
                    }
                pending_attempts.append((item, values))
                results[item.record_id] = _pending_result(item, latest, values)
            output = await session.finalize() if applied else None
            if output is None:
                await session.abort()
        except Exception:
            await session.abort()
            raise

        for item, values in pending_attempts:
            attempt = await self.repository.append_attempt(
                item.record_id,
                status=str(values["status"]),
                error_code=cast(str | None, values.get("error_code")),
                error_detail=cast(Mapping[str, Any] | None, values.get("error_detail")),
                actual_after=cast(Mapping[str, Any] | None, values.get("actual_after")),
                verification=cast(Mapping[str, Any] | None, values.get("verification")),
                retryable=bool(values.get("retryable", False)),
                target_version_id=output.id if output is not None else None,
            )
            results[item.record_id] = _result(item, attempt)

        ordered_results = tuple(
            results[item.record_id] for item in ordered if item.record_id in results
        )
        batch_status = _batch_status(ordered_results)
        retryable_ids = tuple(item.record_id for item in ordered_results if item.retryable)
        await self.repository.append_audit_event(
            batch_id=batch_id,
            actor_id=batch.confirmed_by,
            event_type="batch_execution_finished",
            details={
                "status": batch_status.value,
                "output_target_version_id": (str(output.id) if output is not None else None),
                "retryable_operation_ids": [str(item) for item in retryable_ids],
            },
        )
        return ExecutionBatchResult(
            id=batch_id,
            status=batch_status,
            output_target_version_id=output.id if output is not None else None,
            operations=ordered_results,
            retryable_operation_ids=retryable_ids,
        )

    async def _failed_dependency(
        self,
        operation: GovernanceOperation,
        by_operation_id: dict[UUID, StoredExecutionOperation],
        current_results: dict[UUID, ExecutionOperationResult],
    ) -> UUID | None:
        for dependency_id in operation.dependencies:
            dependency = by_operation_id[dependency_id]
            result = current_results.get(dependency.record_id)
            if result is not None and result.status is not OperationStatus.SUCCEEDED:
                return dependency_id
            if result is None:
                attempts = await self.repository.list_attempts(dependency.record_id)
                if not attempts or attempts[-1].status != OperationStatus.SUCCEEDED.value:
                    return dependency_id
        return None


def _result(
    item: StoredExecutionOperation,
    attempt: AttemptLike,
) -> ExecutionOperationResult:
    return ExecutionOperationResult(
        record_id=item.record_id,
        operation_id=item.operation.id,
        status=OperationStatus(attempt.status),
        attempt_number=attempt.attempt_number,
        retryable=attempt.retryable,
        error_code=attempt.error_code,
        actual_after=attempt.actual_after,
        verification=attempt.verification,
    )


def _pending_result(
    item: StoredExecutionOperation,
    latest: AttemptLike | None,
    values: Mapping[str, Any],
) -> ExecutionOperationResult:
    return ExecutionOperationResult(
        record_id=item.record_id,
        operation_id=item.operation.id,
        status=OperationStatus(str(values["status"])),
        attempt_number=(latest.attempt_number if latest is not None else 0) + 1,
        retryable=bool(values.get("retryable", False)),
        error_code=values.get("error_code"),
        actual_after=values.get("actual_after"),
        verification=values.get("verification"),
    )


def _batch_status(
    results: tuple[ExecutionOperationResult, ...],
) -> ExecutionBatchStatus:
    succeeded = sum(item.status is OperationStatus.SUCCEEDED for item in results)
    if results and succeeded == len(results):
        return ExecutionBatchStatus.SUCCEEDED
    if succeeded:
        return ExecutionBatchStatus.PARTIAL_FAILURE
    return ExecutionBatchStatus.FAILED
