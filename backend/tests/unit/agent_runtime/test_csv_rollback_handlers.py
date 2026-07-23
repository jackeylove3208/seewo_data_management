from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.agent_runtime.csv_rollback_handlers import CsvRollbackHandlers
from app.agent_runtime.state_machine import AgentPhase
from app.agent_runtime.worker import AgentWorkContext
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
