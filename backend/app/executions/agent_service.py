"""Execution of frozen Agent governance plans against the Seewo target only."""

from dataclasses import dataclass
from typing import Protocol, cast
from uuid import UUID

from pydantic import JsonValue

from app.executions.csv_versioning import (
    CsvMutationError,
    CsvTargetVersioner,
    TargetVersionLike,
)
from app.governance.agent_governance import AgentGovernanceOperation, AgentOperation
from app.schemas.canonical_entities import EntityType
from app.schemas.executions import (
    GovernanceOperation,
    OperationType,
    ProposalSource,
    ProposalVersionRef,
)
from app.schemas.governance import RiskLevel


class AgentTargetError(RuntimeError):
    retryable = False


class AgentRetryableTargetError(AgentTargetError):
    retryable = True


class AgentTargetSession(Protocol):
    async def apply_operation(self, operation: GovernanceOperation) -> None: ...
    async def read_entity(self, identifier: str) -> dict[str, object] | None: ...
    async def finalize(self) -> object: ...
    async def abort(self) -> None: ...


class AgentTarget(Protocol):
    async def begin(self, target_version: str, *, plan_id: UUID) -> AgentTargetSession: ...


class AgentOutcomeSink(Protocol):
    async def record_operation_outcome(
        self,
        operation_id: UUID,
        *,
        status: str,
        attempts: int,
        actual_after: dict[str, object] | None = None,
        verification: dict[str, object] | None = None,
        error_code: str | None = None,
    ) -> object: ...


@dataclass(frozen=True)
class AgentOperationResult:
    operation_id: UUID
    status: str
    attempts: int
    actual_after: dict[str, object] | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class AgentExecutionResult:
    plan_id: UUID
    status: str
    output_target_version: object | None
    by_operation: dict[UUID, AgentOperationResult]


class AgentExecutionService:
    def __init__(self, *, max_attempts: int = 4) -> None:
        if max_attempts < 1 or max_attempts > 4:
            raise ValueError("Agent target attempts must be 1..4")
        self.max_attempts = max_attempts

    async def execute(
        self,
        *,
        plan_id: UUID,
        target_version: str,
        operations: tuple[AgentGovernanceOperation, ...],
        target: AgentTarget,
        target_role: str = "target",
        outcome_sink: AgentOutcomeSink | None = None,
    ) -> AgentExecutionResult:
        if target_role != "target":
            raise ValueError("Agent governance may mutate only the Seewo target connector")
        if not operations:
            raise ValueError("Agent governance plan has no operations")
        self._validate_versions(operations, target_version)
        ordered = _stable_order(operations)
        session = await target.begin(target_version, plan_id=plan_id)
        results: dict[UUID, AgentOperationResult] = {}
        committed = False
        try:
            for operation in ordered:
                failed_dependency = next(
                    (
                        dependency
                        for dependency in sorted(operation.dependencies, key=str)
                        if results.get(dependency) is None
                        or results[dependency].status != "succeeded"
                    ),
                    None,
                )
                if failed_dependency is not None:
                    results[operation.id] = AgentOperationResult(
                        operation.id, "blocked", 0, error_code="dependency_failed"
                    )
                    if outcome_sink is not None:
                        await outcome_sink.record_operation_outcome(
                            operation.id,
                            status="blocked",
                            attempts=0,
                            error_code="dependency_failed",
                        )
                    continue
                results[operation.id] = await self._execute_one(session, operation)
                if outcome_sink is not None:
                    result = results[operation.id]
                    await outcome_sink.record_operation_outcome(
                        operation.id,
                        status=result.status,
                        attempts=result.attempts,
                        actual_after=result.actual_after,
                        verification={"valid": result.status == "succeeded"},
                        error_code=result.error_code,
                    )
                committed = committed or results[operation.id].status == "succeeded"
            output = await session.finalize() if committed else None
            if output is None:
                await session.abort()
        except Exception:
            await session.abort()
            raise
        statuses = tuple(item.status for item in results.values())
        if all(status == "succeeded" for status in statuses):
            status = "succeeded"
        elif any(status == "succeeded" for status in statuses):
            status = "partial"
        else:
            status = "failed"
        return AgentExecutionResult(plan_id, status, output, results)

    async def _execute_one(
        self, session: AgentTargetSession, operation: AgentGovernanceOperation
    ) -> AgentOperationResult:
        converted = _to_governance_operation(operation)
        attempts = 0
        while attempts < self.max_attempts:
            attempts += 1
            try:
                await session.apply_operation(converted)
                identifier = operation.target_source_identifier
                if operation.operation == AgentOperation.CREATE:
                    identifier = str((operation.after or {}).get("source_id") or "")
                actual = await session.read_entity(identifier or "")
                if operation.operation == AgentOperation.DELETE:
                    valid = actual is None
                else:
                    expected = dict(operation.after or {})
                    if operation.operation == AgentOperation.CREATE:
                        expected.pop("source_id", None)
                    valid = actual is not None and all(
                        _verification_values_match(actual.get(field), value)
                        for field, value in expected.items()
                    )
                if not valid:
                    return AgentOperationResult(
                        operation.id, "verification_failed", attempts, actual_after=actual
                    )
                return AgentOperationResult(
                    operation.id, "succeeded", attempts, actual_after=actual
                )
            except AgentTargetError as error:
                if not error.retryable or attempts >= self.max_attempts:
                    return AgentOperationResult(
                        operation.id, "failed", attempts, error_code=type(error).__name__
                    )
        return AgentOperationResult(operation.id, "failed", attempts, error_code="retry_exhausted")

    @staticmethod
    def _validate_versions(
        operations: tuple[AgentGovernanceOperation, ...], target_version: str
    ) -> None:
        if any(operation.target_version != target_version for operation in operations):
            raise ValueError("Agent plan target version is stale")


class CsvAgentTargetAdapter:
    """Bind the generic Agent executor to the existing immutable CSV versioner."""

    def __init__(self, *, versioner: CsvTargetVersioner, parent: TargetVersionLike) -> None:
        self.versioner = versioner
        self.parent = parent

    async def begin(self, target_version: str, *, plan_id: UUID) -> AgentTargetSession:
        expected = f"sha256:{self.parent.file_sha256}"
        if target_version != expected:
            raise ValueError("target version is stale")
        return _CsvAgentTargetSession(
            await self.versioner.begin(self.parent, batch_id=plan_id)
        )


class _CsvAgentTargetSession:
    def __init__(self, session: AgentTargetSession) -> None:
        self._session = session

    async def apply_operation(self, operation: GovernanceOperation) -> None:
        try:
            await self._session.apply_operation(operation)
        except CsvMutationError as error:
            raise AgentTargetError("CSV operation failed a frozen target guard") from error

    async def read_entity(self, identifier: str) -> dict[str, object] | None:
        return await self._session.read_entity(identifier)

    async def finalize(self) -> object:
        return await self._session.finalize()

    async def abort(self) -> None:
        await self._session.abort()


def _to_governance_operation(operation: AgentGovernanceOperation) -> GovernanceOperation:
    entity = {
        "department": EntityType.ORGANIZATION_UNIT,
        "teacher": EntityType.TEACHER,
        "student": EntityType.STUDENT,
    }.get(operation.entity_kind)
    if entity is None:
        raise ValueError("unsupported Agent entity kind")
    operation_type = {
        AgentOperation.CREATE: OperationType.CREATE,
        AgentOperation.UPDATE: OperationType.UPDATE,
        # Legacy execution records intentionally keep their executable enum stable;
        # an Agent delete is represented as a compensating absence operation.
        AgentOperation.DELETE: OperationType.DISABLE,
    }.get(operation.operation)
    if operation_type is None:
        raise ValueError("non-executable Agent operation")
    return GovernanceOperation(
        id=operation.id,
        proposal=ProposalVersionRef(proposal_id=operation.finding_id, proposal_version=1),
        proposal_source=ProposalSource.AI,
        difference_id=operation.finding_id,
        difference_version=1,
        analysis_id=operation.finding_id,
        analysis_version="agent-analysis-v1",
        operation_type=operation_type,
        entity_type=entity,
        target_source_identifier=operation.target_source_identifier,
        before=cast(dict[str, JsonValue] | None, operation.before),
        after=(
            {}
            if operation.operation == AgentOperation.DELETE
            else cast(dict[str, JsonValue] | None, operation.after)
        ),
        changed_fields=frozenset(operation.after or operation.before or {}),
        dependencies=operation.dependencies,
        reversible=False if operation.operation == AgentOperation.DELETE else True,
        risk=RiskLevel(operation.risk),
        compensation_for=operation.finding_id
        if operation.operation == AgentOperation.DELETE
        else None,
        restore_absence=operation.operation == AgentOperation.DELETE,
    )


def _stable_order(
    operations: tuple[AgentGovernanceOperation, ...],
) -> tuple[AgentGovernanceOperation, ...]:
    by_id = {operation.id: operation for operation in operations}
    if len(by_id) != len(operations):
        raise ValueError("duplicate Agent operation id")
    remaining = {operation.id: set(operation.dependencies) for operation in operations}
    if any(dependencies - by_id.keys() for dependencies in remaining.values()):
        raise ValueError("Agent plan contains a missing dependency")
    ordered: list[AgentGovernanceOperation] = []
    while remaining:
        ready = sorted(
            (operation_id for operation_id, dependencies in remaining.items() if not dependencies),
            key=str,
        )
        if not ready:
            raise ValueError("Agent plan dependency graph contains a cycle")
        for operation_id in ready:
            ordered.append(by_id[operation_id])
            remaining.pop(operation_id)
        for dependencies in remaining.values():
            dependencies.difference_update(ready)
    return tuple(ordered)


def _verification_values_match(actual: object, expected: object) -> bool:
    if expected is None:
        return actual is None or actual == ""
    return actual == expected
