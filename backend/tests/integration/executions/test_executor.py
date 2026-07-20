from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.executions.executor import (
    ExecutionExecutor,
    RetryableConnectorError,
    StoredExecutionOperation,
)
from app.executions.verifier import TargetVerifier
from app.schemas.canonical_entities import EntityType
from app.schemas.executions import (
    ExecutionBatchStatus,
    GovernanceOperation,
    OperationStatus,
    OperationType,
    ProposalSource,
    ProposalVersionRef,
)
from app.schemas.governance import RiskLevel


def update_operation(target: str, after: str) -> GovernanceOperation:
    return GovernanceOperation(
        proposal=ProposalVersionRef(proposal_id=uuid4(), proposal_version=1),
        proposal_source=ProposalSource.AI,
        difference_id=uuid4(),
        difference_version=1,
        analysis_id=uuid4(),
        analysis_version="analysis-v2",
        operation_type=OperationType.UPDATE,
        entity_type=EntityType.TEACHER,
        target_source_identifier=target,
        before={"phone": "old"},
        after={"phone": after},
        changed_fields=frozenset({"phone"}),
        reversible=True,
        risk=RiskLevel.MEDIUM,
    )


class ExecutionRepositoryFake:
    def __init__(self, operations: tuple[GovernanceOperation, ...]) -> None:
        self.batch = SimpleNamespace(
            id=uuid4(),
            input_target_version_id=uuid4(),
            confirmed_by="demo-operator",
        )
        self.parent = SimpleNamespace(id=self.batch.input_target_version_id)
        self.operations = tuple(
            StoredExecutionOperation(record_id=uuid4(), operation=item) for item in operations
        )
        self.attempts: dict[UUID, list[SimpleNamespace]] = {
            item.record_id: [] for item in self.operations
        }
        self.events: list[dict[str, object]] = []
        self.latest_parent = None

    async def get_batch(self, batch_id: UUID):
        return self.batch if batch_id == self.batch.id else None

    async def get_target_version(self, version_id: UUID):
        return self.parent if version_id == self.parent.id else None

    async def retry_target_version(self, batch_id: UUID):
        assert batch_id == self.batch.id
        return self.latest_parent or self.parent

    async def execution_operations(self, batch_id: UUID):
        assert batch_id == self.batch.id
        return self.operations

    async def list_attempts(self, operation_id: UUID):
        return tuple(self.attempts[operation_id])

    async def append_attempt(self, operation_id: UUID, **values):
        attempt = SimpleNamespace(
            **{
                "id": uuid4(),
                "attempt_number": len(self.attempts[operation_id]) + 1,
                "operation_id": operation_id,
                "error_code": None,
                "retryable": False,
                "actual_after": None,
                "verification": None,
                **values,
            }
        )
        self.attempts[operation_id].append(attempt)
        return attempt

    async def append_audit_event(self, **values):
        self.events.append(values)
        return SimpleNamespace(id=uuid4(), **values)


class MutationSessionStub:
    def __init__(self, rows: dict[str, dict[str, object]]) -> None:
        self.rows = rows
        self.failures: dict[UUID, Exception] = {}
        self.wrong_values: set[UUID] = set()
        self.current_operation: GovernanceOperation | None = None
        self.finalized = False

    async def apply_operation(self, operation: GovernanceOperation) -> None:
        self.current_operation = operation
        if error := self.failures.get(operation.id):
            raise error
        assert operation.target_source_identifier is not None
        self.rows[operation.target_source_identifier].update(operation.after or {})

    async def read_entity(self, identifier: str):
        value = dict(self.rows[identifier])
        if self.current_operation is not None and self.current_operation.id in self.wrong_values:
            value["phone"] = "wrong"
        return value

    async def finalize(self):
        self.finalized = True
        return SimpleNamespace(id=uuid4())

    async def abort(self) -> None:
        self.finalized = False


class TargetStub:
    def __init__(self, session: MutationSessionStub) -> None:
        self.session = session
        self.parent_ids: list[UUID] = []

    async def begin(self, parent, *, batch_id: UUID):
        self.parent_ids.append(parent.id)
        return self.session


@pytest.mark.asyncio
async def test_unrelated_operation_continues_after_retryable_failure() -> None:
    operations = tuple(
        update_operation(target, after)
        for target, after in (("T1", "one"), ("T2", "two"), ("T3", "three"))
    )
    repository = ExecutionRepositoryFake(operations)
    session = MutationSessionStub({target: {"phone": "old"} for target in ("T1", "T2", "T3")})
    session.failures[operations[1].id] = RetryableConnectorError("timeout")
    executor = ExecutionExecutor(
        repository=repository,
        target=TargetStub(session),
        verifier=TargetVerifier(),
    )

    result = await executor.execute(repository.batch.id)

    assert result.status is ExecutionBatchStatus.PARTIAL_FAILURE
    by_operation = {item.operation_id: item for item in result.operations}
    assert by_operation[operations[0].id].status is OperationStatus.SUCCEEDED
    assert by_operation[operations[1].id].status is OperationStatus.FAILED
    assert by_operation[operations[2].id].status is OperationStatus.SUCCEEDED
    assert by_operation[operations[1].id].retryable is True
    assert session.rows["T1"]["phone"] == "one"
    assert session.rows["T3"]["phone"] == "three"
    assert session.finalized is True


@pytest.mark.asyncio
async def test_wrong_reloaded_state_is_verification_failed() -> None:
    operation = update_operation("T1", "expected")
    repository = ExecutionRepositoryFake((operation,))
    session = MutationSessionStub({"T1": {"phone": "old"}})
    session.wrong_values.add(operation.id)
    executor = ExecutionExecutor(
        repository=repository,
        target=TargetStub(session),
        verifier=TargetVerifier(),
    )

    result = await executor.execute(repository.batch.id)

    assert result.status is ExecutionBatchStatus.FAILED
    assert result.operations[0].status is OperationStatus.VERIFICATION_FAILED
    assert result.operations[0].verification is not None
    assert result.operations[0].verification["mismatches"]["phone"] == {
        "expected": "expected",
        "actual": "wrong",
    }


@pytest.mark.asyncio
async def test_retry_runs_only_retryable_failure_and_appends_attempt() -> None:
    operation = update_operation("T1", "expected")
    repository = ExecutionRepositoryFake((operation,))
    session = MutationSessionStub({"T1": {"phone": "old"}})
    session.failures[operation.id] = RetryableConnectorError("timeout")
    target = TargetStub(session)
    executor = ExecutionExecutor(
        repository=repository,
        target=target,
        verifier=TargetVerifier(),
    )
    first = await executor.execute(repository.batch.id)
    assert first.operations[0].retryable is True
    session.failures.clear()
    repository.latest_parent = SimpleNamespace(id=uuid4())

    retried = await executor.execute(
        repository.batch.id,
        retry_only=frozenset({repository.operations[0].record_id}),
    )

    assert retried.status is ExecutionBatchStatus.SUCCEEDED
    assert target.parent_ids[-1] == repository.latest_parent.id
    assert retried.operations[0].attempt_number == 2
    assert [
        attempt.status for attempt in repository.attempts[repository.operations[0].record_id]
    ] == ["failed", "succeeded"]


@pytest.mark.asyncio
async def test_reexecution_does_not_repeat_a_succeeded_operation() -> None:
    operation = update_operation("T1", "expected")
    repository = ExecutionRepositoryFake((operation,))
    session = MutationSessionStub({"T1": {"phone": "old"}})
    executor = ExecutionExecutor(
        repository=repository,
        target=TargetStub(session),
        verifier=TargetVerifier(),
    )
    await executor.execute(repository.batch.id)

    replay = await executor.execute(repository.batch.id)

    assert replay.status is ExecutionBatchStatus.SUCCEEDED
    assert len(repository.attempts[repository.operations[0].record_id]) == 1
