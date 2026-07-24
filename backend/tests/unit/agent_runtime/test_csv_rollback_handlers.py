from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.agent_runtime.csv_rollback_handlers import (
    CsvRollbackHandlers,
    _rollback_operation,
)
from app.agent_runtime.repository import AgentRuntimeRepository
from app.agent_runtime.state_machine import AgentPhase
from app.agent_runtime.worker import AgentWorkContext
from app.executions.agent_service import AgentExecutionService
from app.models.executions import TargetVersionRecord
from app.models.reconciliation import ReconciliationTask
from app.repositories.executions import ExecutionRepository


class _Session:
    def __init__(self, task: object, target: object | None) -> None:
        self._task = task
        self._target = target

    async def get(self, model: type[object], _identifier: object) -> object | None:
        if model is ReconciliationTask:
            return self._task
        if model is TargetVersionRecord:
            return self._target
        raise AssertionError(f"unexpected model: {model}")


def _context(task_id):
    return AgentWorkContext(
        run_id=uuid4(),
        task_id=task_id,
        tenant_id="school-1",
        phase=AgentPhase.PLAN_RESTORE,
        attempt_count=1,
        lease_token=uuid4(),
        worker_id="rollback-test-worker",
    )


@pytest.mark.asyncio
async def test_rollback_plan_fails_closed_when_version_artifact_is_missing(tmp_path) -> None:
    task_id = uuid4()
    task = SimpleNamespace(
        id=task_id,
        agent_intent={
            "target_version_id": str(uuid4()),
            "operations": [{"id": str(uuid4())}],
        },
    )

    with pytest.raises(LookupError, match="target version is missing"):
        await CsvRollbackHandlers(output_root=tmp_path).plan(
            _Session(task, None),  # type: ignore[arg-type]
            _context(task_id),
        )


@pytest.mark.asyncio
async def test_rollback_plan_rejects_intervening_target_version(
    monkeypatch, tmp_path
) -> None:
    task_id = uuid4()
    expected = SimpleNamespace(id=uuid4(), task_id=uuid4())
    intervening = SimpleNamespace(id=uuid4())
    task = SimpleNamespace(
        id=task_id,
        agent_intent={
            "target_version_id": str(expected.id),
            "operations": [{"id": str(uuid4())}],
        },
    )

    async def current_target_version(_repository, _task_id):
        return intervening

    monkeypatch.setattr(
        ExecutionRepository,
        "current_target_version",
        current_target_version,
    )

    with pytest.raises(ValueError, match="intervening change"):
        await CsvRollbackHandlers(output_root=tmp_path).plan(
            _Session(task, expected),  # type: ignore[arg-type]
            _context(task_id),
        )


@pytest.mark.asyncio
async def test_execute_operation_mutates_only_the_requested_rollback_operation(
    monkeypatch,
    tmp_path,
) -> None:
    task_id = uuid4()
    first_mutation_id = uuid4()
    second_mutation_id = uuid4()
    mutations = [
        {
            "id": str(first_mutation_id),
            "operation": "update",
            "entity_kind": "student",
            "target_source_identifier": "student-1",
            "before": {"name": "原姓名"},
            "after": {"name": "新姓名"},
        },
        {
            "id": str(second_mutation_id),
            "operation": "update",
            "entity_kind": "student",
            "target_source_identifier": "student-2",
            "before": {"name": "原姓名二"},
            "after": {"name": "新姓名二"},
        },
    ]
    parent = SimpleNamespace(
        id=uuid4(),
        file_sha256="a" * 64,
        task_id=uuid4(),
        storage_path=str(tmp_path / "target.csv"),
    )
    task = SimpleNamespace(
        id=task_id,
        request_hash="request-hash",
        parent_task_id=uuid4(),
        agent_intent={
            "target_version_id": str(parent.id),
            "operations": mutations,
        },
    )
    selected = _rollback_operation(
        mutations[0],
        target_version=f"sha256:{parent.file_sha256}",
    )
    executed_operation_ids = []

    async def get_checkpoint(_repository, *_args, **_kwargs):
        return None

    async def save_checkpoint(_repository, *_args, **kwargs):
        return SimpleNamespace(payload=kwargs["payload"])

    async def execute(_service, **kwargs):
        operations = kwargs["operations"]
        executed_operation_ids.extend(item.id for item in operations)
        result = SimpleNamespace(
            operation_id=operations[0].id,
            status="succeeded",
        )
        return SimpleNamespace(
            output_target_version=None,
            by_operation={operations[0].id: result},
        )

    monkeypatch.setattr(AgentRuntimeRepository, "get_checkpoint", get_checkpoint)
    monkeypatch.setattr(AgentRuntimeRepository, "save_checkpoint", save_checkpoint)
    monkeypatch.setattr(AgentExecutionService, "execute", execute)

    fact = await CsvRollbackHandlers(output_root=tmp_path).execute_operation(
        _Session(task, parent),  # type: ignore[arg-type]
        _context(task_id),
        selected.id,
    )

    assert executed_operation_ids == [selected.id]
    assert fact["id"] == str(selected.id)
    assert str(second_mutation_id) not in str(fact)
