from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.agent_reporting.service import AgentReportingService
from app.agent_runtime.csv_rollback_handlers import (
    CsvRollbackHandlers,
    _rollback_operation,
    _rollback_operations,
    compare_csv_rollback_mutation,
)
from app.agent_runtime.repository import AgentRuntimeRepository
from app.agent_runtime.state_machine import AgentPhase
from app.agent_runtime.worker import AgentWorkContext
from app.core.config import Settings
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
async def test_rollback_plan_uses_current_values_when_target_version_advanced(
    monkeypatch, tmp_path
) -> None:
    task_id = uuid4()
    target_path = tmp_path / "current.csv"
    target_path.write_text(
        "id,姓名,手机号,班级\nstudent-1,张三,13800000000,二班\n",
        encoding="utf-8",
    )
    expected = SimpleNamespace(id=uuid4(), task_id=uuid4())
    intervening = SimpleNamespace(id=uuid4(), storage_path=str(target_path))
    mutation_id = uuid4()
    task = SimpleNamespace(
        id=task_id,
        parent_task_id=uuid4(),
        request_hash="request-hash",
        agent_intent={
            "target_version_id": str(expected.id),
            "operations": [
                {
                    "id": str(mutation_id),
                    "operation": "update",
                    "entity_kind": "student",
                    "target_source_identifier": "student-1",
                    "before": {"phone": ""},
                    "after": {"phone": "13800000000"},
                }
            ],
        },
    )
    saved_payload = None

    async def current_target_version(_repository, _task_id):
        return intervening

    async def save_checkpoint(_repository, *_args, **kwargs):
        nonlocal saved_payload
        saved_payload = kwargs["payload"]
        return SimpleNamespace(payload=saved_payload)

    monkeypatch.setattr(
        ExecutionRepository,
        "current_target_version",
        current_target_version,
    )
    monkeypatch.setattr(AgentRuntimeRepository, "save_checkpoint", save_checkpoint)

    result = await CsvRollbackHandlers(output_root=tmp_path).plan(
        _Session(task, expected),  # type: ignore[arg-type]
        _context(task_id),
    )

    assert result.next_phase == AgentPhase.CLARIFY_RESTORE_CONFLICTS
    assert saved_payload is not None
    assert saved_payload["restore_comparisons"] == [
        {
            "operation_id": str(mutation_id),
            "disposition": "safe_to_restore",
            "reason_code": "current_matches_after",
            "affected_fields": ["phone"],
            "comparison_hash": saved_payload["restore_comparisons"][0][
                "comparison_hash"
            ],
        }
    ]


@pytest.mark.asyncio
async def test_rollback_plan_recovers_missing_version_artifact_from_live_local_target(
    monkeypatch,
    tmp_path,
) -> None:
    task_id = uuid4()
    source_task_id = uuid4()
    missing_path = tmp_path / "missing-version.csv"
    live_path = tmp_path / "live-target.csv"
    live_path.write_text(
        "id,姓名,手机号\nstudent-1,张三,13800000000\n",
        encoding="utf-8",
    )
    target = SimpleNamespace(
        id=uuid4(),
        task_id=source_task_id,
        tenant_id="school-1",
        source_snapshot_id=uuid4(),
        file_sha256="a" * 64,
        storage_path=str(missing_path),
    )
    mutation_id = uuid4()
    task = SimpleNamespace(
        id=task_id,
        parent_task_id=source_task_id,
        request_hash="request-hash",
        agent_intent={
            "target_version_id": str(target.id),
            "target": {
                "kind": "local",
                "source_ref": live_path.name,
            },
            "operations": [
                {
                    "id": str(mutation_id),
                    "operation": "update",
                    "entity_kind": "student",
                    "target_source_identifier": "student-1",
                    "before": {"phone": "13800000000"},
                    "after": {"phone": "13900000000"},
                }
            ],
        },
    )
    saved_payload = None
    created_version_values = None
    recovered = SimpleNamespace(
        id=uuid4(),
        task_id=source_task_id,
        tenant_id="school-1",
        source_snapshot_id=target.source_snapshot_id,
        file_sha256="recovered",
        storage_path="",
    )

    async def current_target_version(_repository, _task_id):
        return target

    async def create_target_version(_repository, **values):
        nonlocal created_version_values
        created_version_values = values
        recovered.file_sha256 = values["file_sha256"]
        recovered.storage_path = str(values["storage_path"])
        return recovered

    async def save_checkpoint(_repository, *_args, **kwargs):
        nonlocal saved_payload
        saved_payload = kwargs["payload"]
        return SimpleNamespace(payload=saved_payload)

    monkeypatch.setattr(
        ExecutionRepository,
        "current_target_version",
        current_target_version,
    )
    monkeypatch.setattr(
        ExecutionRepository,
        "create_target_version",
        create_target_version,
    )
    monkeypatch.setattr(AgentRuntimeRepository, "save_checkpoint", save_checkpoint)

    settings = Settings(
        agent_local_read_roots=(tmp_path,),
        agent_local_write_roots=(tmp_path,),
    )
    result = await CsvRollbackHandlers(
        output_root=tmp_path / "versions",
        settings=settings,
    ).plan(
        _Session(task, target),  # type: ignore[arg-type]
        _context(task_id),
    )

    assert result.next_phase == AgentPhase.CLARIFY_RESTORE_CONFLICTS
    assert created_version_values is not None
    assert created_version_values["parent_version_id"] == target.id
    assert created_version_values["storage_path"] != live_path
    assert created_version_values["storage_path"].read_bytes() == live_path.read_bytes()
    assert saved_payload["comparison_target_version_id"] == str(recovered.id)
    assert saved_payload["restore_comparisons"][0]["disposition"] == "already_restored"


@pytest.mark.asyncio
async def test_rollback_report_does_not_mark_conflict_skips_completed(
    monkeypatch,
    tmp_path,
) -> None:
    task_id = uuid4()
    task = SimpleNamespace(
        id=task_id,
        tenant_id="school-1",
        status="running",
        stage="execute_restore",
    )
    checkpoint = SimpleNamespace(
        payload={
            "mutations": [
                {
                    "id": str(uuid4()),
                    "status": "conflict_skipped",
                    "verification": {"valid": False},
                }
            ]
        }
    )
    generated_terminal_state = None

    async def get_checkpoint(_repository, *_args, **_kwargs):
        return checkpoint

    async def generate(_service, **kwargs):
        nonlocal generated_terminal_state
        generated_terminal_state = kwargs["terminal_state"]
        return SimpleNamespace(id=uuid4(), terminal_state=generated_terminal_state)

    async def append_event(_repository, *_args, **_kwargs):
        return None

    monkeypatch.setattr(AgentRuntimeRepository, "get_checkpoint", get_checkpoint)
    monkeypatch.setattr(AgentRuntimeRepository, "append_event", append_event)
    monkeypatch.setattr(AgentReportingService, "generate", generate)

    await CsvRollbackHandlers(output_root=tmp_path).report(
        _Session(task, None),  # type: ignore[arg-type]
        _context(task_id),
    )

    assert generated_terminal_state == "completed_with_conflicts"


def test_update_comparison_distinguishes_after_before_and_conflict_by_affected_fields() -> None:
    mutation_id = uuid4()
    mutation = {
        "id": str(mutation_id),
        "operation": "update",
        "entity_kind": "student",
        "target_source_identifier": "student-1",
        "before": {"phone": "A"},
        "after": {"phone": "B"},
    }

    safe = compare_csv_rollback_mutation(
        mutation,
        current={"source_id": "student-1", "phone": "B", "class_name": "二班"},
    )
    already_restored = compare_csv_rollback_mutation(
        mutation,
        current={"source_id": "student-1", "phone": "A", "class_name": "三班"},
    )
    conflict = compare_csv_rollback_mutation(
        mutation,
        current={"source_id": "student-1", "phone": "C", "class_name": "四班"},
    )

    assert safe["disposition"] == "safe_to_restore"
    assert safe["affected_fields"] == ["phone"]
    assert already_restored["disposition"] == "already_restored"
    assert conflict["disposition"] == "conflict"
    assert safe["comparison_hash"] != already_restored["comparison_hash"]
    assert conflict["reason_code"] == "affected_fields_changed"


@pytest.mark.parametrize(
    ("operation", "current", "expected_disposition"),
    [
        (
            {
                "operation": "create",
                "target_source_identifier": "student-created",
                "before": None,
                "after": {"name": "新学生", "phone": "13800000000"},
            },
            None,
            "already_restored",
        ),
        (
            {
                "operation": "delete",
                "target_source_identifier": "student-deleted",
                "before": {"name": "被删学生", "phone": ""},
                "after": None,
            },
            None,
            "safe_to_restore",
        ),
    ],
)
def test_create_and_delete_comparison_handle_record_absence(
    operation, current, expected_disposition
) -> None:
    mutation = {
        "id": str(uuid4()),
        "entity_kind": "student",
        **operation,
    }

    result = compare_csv_rollback_mutation(mutation, current=current)

    assert result["disposition"] == expected_disposition


def test_create_comparison_detects_later_custom_column_data() -> None:
    mutation = {
        "id": str(uuid4()),
        "operation": "create",
        "entity_kind": "student",
        "target_source_identifier": "student-created",
        "before": None,
        "after": {"name": "新学生", "phone": "13800000000"},
    }
    unchanged = compare_csv_rollback_mutation(
        mutation,
        current={
            "id": "student-created",
            "source_id": "student-created",
            "姓名": "新学生",
            "name": "新学生",
            "手机号": "13800000000",
            "phone": "13800000000",
            "备注": "",
        },
    )
    changed = compare_csv_rollback_mutation(
        mutation,
        current={
            "id": "student-created",
            "source_id": "student-created",
            "姓名": "新学生",
            "name": "新学生",
            "手机号": "13800000000",
            "phone": "13800000000",
            "备注": "同步后新增的说明",
        },
    )

    assert unchanged["disposition"] == "safe_to_restore"
    assert changed["disposition"] == "conflict"
    assert changed["reason_code"] == "created_record_changed"
    assert unchanged["comparison_hash"] != changed["comparison_hash"]
    assert "备注" in changed["affected_fields"]


def test_delete_comparison_requires_complete_physical_before_fact() -> None:
    mutation = {
        "id": str(uuid4()),
        "operation": "delete",
        "entity_kind": "student",
        "target_source_identifier": "student-deleted",
        "before": {
            "source_id": "student-deleted",
            "name": "被删学生",
        },
        "after": None,
    }

    result = compare_csv_rollback_mutation(
        mutation,
        current=None,
        complete_record_fields={"id", "姓名", "备注"},
    )

    assert result["disposition"] == "conflict"
    assert result["reason_code"] == "complete_record_fact_missing"


def test_rollback_operations_reverse_only_real_business_dependencies() -> None:
    parent_id = uuid4()
    child_id = uuid4()
    mutations = (
        {
            "id": str(parent_id),
            "operation": "create",
            "entity_kind": "department",
            "target_source_identifier": "department-1",
            "before": None,
            "after": {"name": "部门"},
            "dependencies": [],
        },
        {
            "id": str(child_id),
            "operation": "create",
            "entity_kind": "teacher",
            "target_source_identifier": "teacher-1",
            "before": None,
            "after": {"name": "教师"},
            "dependencies": [str(parent_id)],
        },
    )

    operations = _rollback_operations(
        mutations,
        target_version="sha256:" + "a" * 64,
    )
    child_rollback_id = _rollback_operation(
        mutations[1],
        target_version="ignored",
    ).id

    assert [operation.finding_id for operation in operations] == [
        child_id,
        parent_id,
    ]
    assert operations[0].dependencies == frozenset()
    assert operations[1].dependencies == frozenset(
        {child_rollback_id}
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
    (tmp_path / "target.csv").write_text(
        "id,姓名\nstudent-1,新姓名\nstudent-2,新姓名二\n",
        encoding="utf-8",
    )
    task = SimpleNamespace(
        id=task_id,
        request_hash="request-hash",
        parent_task_id=uuid4(),
        agent_intent={
            "target_version_id": str(parent.id),
            "operations": mutations,
            "restore_comparisons": [
                compare_csv_rollback_mutation(
                    mutation,
                    current={
                        "source_id": mutation["target_source_identifier"],
                        "name": mutation["after"]["name"],
                    },
                )
                for mutation in mutations
            ],
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

    async def current_target_version(_repository, _task_id):
        return parent

    monkeypatch.setattr(AgentRuntimeRepository, "get_checkpoint", get_checkpoint)
    monkeypatch.setattr(AgentRuntimeRepository, "save_checkpoint", save_checkpoint)
    monkeypatch.setattr(AgentExecutionService, "execute", execute)
    monkeypatch.setattr(
        ExecutionRepository,
        "current_target_version",
        current_target_version,
    )

    fact = await CsvRollbackHandlers(output_root=tmp_path).execute_operation(
        _Session(task, parent),  # type: ignore[arg-type]
        _context(task_id),
        selected.id,
    )

    assert executed_operation_ids == [selected.id]
    assert fact["id"] == str(selected.id)
    assert str(second_mutation_id) not in str(fact)


@pytest.mark.asyncio
async def test_execute_operation_reports_already_restored_without_writing(
    monkeypatch,
    tmp_path,
) -> None:
    task_id = uuid4()
    mutation = {
        "id": str(uuid4()),
        "operation": "update",
        "entity_kind": "student",
        "target_source_identifier": "student-1",
        "before": {"phone": "A"},
        "after": {"phone": "B"},
    }
    current_path = tmp_path / "already-restored.csv"
    current_path.write_text(
        "id,手机号,班级\nstudent-1,A,二班\n",
        encoding="utf-8",
    )
    parent = SimpleNamespace(
        id=uuid4(),
        file_sha256="a" * 64,
        task_id=uuid4(),
        storage_path=str(tmp_path / "original.csv"),
    )
    current = SimpleNamespace(
        id=uuid4(),
        file_sha256="b" * 64,
        task_id=parent.task_id,
        storage_path=str(current_path),
    )
    planned = compare_csv_rollback_mutation(
        mutation,
        current={"source_id": "student-1", "phone": "A", "class_name": "一班"},
    )
    task = SimpleNamespace(
        id=task_id,
        request_hash="request-hash",
        parent_task_id=uuid4(),
        agent_intent={
            "target_version_id": str(parent.id),
            "operations": [mutation],
            "restore_comparisons": [planned],
        },
    )
    saved_payloads = []

    async def get_checkpoint(_repository, *_args, **_kwargs):
        return None

    async def save_checkpoint(_repository, *_args, **kwargs):
        saved_payloads.append(kwargs["payload"])
        return SimpleNamespace(payload=kwargs["payload"])

    async def current_target_version(_repository, _task_id):
        return current

    async def execute(*_args, **_kwargs):
        raise AssertionError("already-restored operation must not write")

    monkeypatch.setattr(AgentRuntimeRepository, "get_checkpoint", get_checkpoint)
    monkeypatch.setattr(AgentRuntimeRepository, "save_checkpoint", save_checkpoint)
    monkeypatch.setattr(
        ExecutionRepository,
        "current_target_version",
        current_target_version,
    )
    monkeypatch.setattr(AgentExecutionService, "execute", execute)

    operation = _rollback_operation(
        mutation,
        target_version=f"sha256:{parent.file_sha256}",
    )
    fact = await CsvRollbackHandlers(output_root=tmp_path).execute_operation(
        _Session(task, parent),  # type: ignore[arg-type]
        _context(task_id),
        operation.id,
    )

    assert fact["status"] == "already_restored"
    assert fact["verification"]["valid"] is True
    assert fact["verification"]["no_write"] is True
    assert saved_payloads == [fact]


@pytest.mark.asyncio
async def test_execute_operation_rejects_related_drift_before_writing(
    monkeypatch,
    tmp_path,
) -> None:
    task_id = uuid4()
    mutation = {
        "id": str(uuid4()),
        "operation": "update",
        "entity_kind": "student",
        "target_source_identifier": "student-1",
        "before": {"phone": "A"},
        "after": {"phone": "B"},
    }
    current_path = tmp_path / "drifted.csv"
    current_path.write_text(
        "id,手机号,班级\nstudent-1,C,二班\n",
        encoding="utf-8",
    )
    parent = SimpleNamespace(
        id=uuid4(),
        file_sha256="a" * 64,
        task_id=uuid4(),
        storage_path=str(tmp_path / "original.csv"),
    )
    current = SimpleNamespace(
        id=uuid4(),
        file_sha256="b" * 64,
        task_id=parent.task_id,
        storage_path=str(current_path),
    )
    planned = compare_csv_rollback_mutation(
        mutation,
        current={"source_id": "student-1", "phone": "B", "class_name": "一班"},
    )
    task = SimpleNamespace(
        id=task_id,
        request_hash="request-hash",
        parent_task_id=uuid4(),
        agent_intent={
            "target_version_id": str(parent.id),
            "operations": [mutation],
            "restore_comparisons": [planned],
        },
    )

    async def get_checkpoint(_repository, *_args, **_kwargs):
        return None

    async def save_checkpoint(_repository, *_args, **kwargs):
        return SimpleNamespace(payload=kwargs["payload"])

    async def current_target_version(_repository, _task_id):
        return current

    async def execute(*_args, **_kwargs):
        raise AssertionError("drifted affected data must not be overwritten")

    monkeypatch.setattr(AgentRuntimeRepository, "get_checkpoint", get_checkpoint)
    monkeypatch.setattr(AgentRuntimeRepository, "save_checkpoint", save_checkpoint)
    monkeypatch.setattr(
        ExecutionRepository,
        "current_target_version",
        current_target_version,
    )
    monkeypatch.setattr(AgentExecutionService, "execute", execute)

    operation = _rollback_operation(
        mutation,
        target_version=f"sha256:{parent.file_sha256}",
    )
    fact = await CsvRollbackHandlers(output_root=tmp_path).execute_operation(
        _Session(task, parent),  # type: ignore[arg-type]
        _context(task_id),
        operation.id,
    )

    assert fact["status"] == "conflict_skipped"
    assert fact["safe_error_code"] == "rollback_current_data_conflict"


@pytest.mark.asyncio
async def test_independent_operation_continues_after_prior_conflict(
    monkeypatch,
    tmp_path,
) -> None:
    task_id = uuid4()
    first_mutation = {
        "id": str(uuid4()),
        "operation": "update",
        "entity_kind": "student",
        "target_source_identifier": "student-1",
        "before": {"phone": "A"},
        "after": {"phone": "B"},
    }
    second_mutation = {
        "id": str(uuid4()),
        "operation": "update",
        "entity_kind": "student",
        "target_source_identifier": "student-2",
        "before": {"phone": "C"},
        "after": {"phone": "D"},
    }
    current_path = tmp_path / "independent.csv"
    current_path.write_text(
        "id,手机号\nstudent-1,changed\nstudent-2,D\n",
        encoding="utf-8",
    )
    parent = SimpleNamespace(
        id=uuid4(),
        file_sha256="a" * 64,
        task_id=uuid4(),
        storage_path=str(current_path),
    )
    task = SimpleNamespace(
        id=task_id,
        request_hash="request-hash",
        parent_task_id=uuid4(),
        agent_intent={
            "target_version_id": str(parent.id),
            "operations": [first_mutation, second_mutation],
            "restore_comparisons": [
                compare_csv_rollback_mutation(
                    first_mutation,
                    current={"source_id": "student-1", "phone": "B"},
                ),
                compare_csv_rollback_mutation(
                    second_mutation,
                    current={"source_id": "student-2", "phone": "D"},
                ),
            ],
        },
    )
    first_operation = _rollback_operation(
        first_mutation,
        target_version=f"sha256:{parent.file_sha256}",
    )
    second_operation = _rollback_operation(
        second_mutation,
        target_version=f"sha256:{parent.file_sha256}",
    )
    executed: list[object] = []

    async def get_checkpoint(_repository, *_args, **kwargs):
        checkpoint_key = kwargs["checkpoint_key"]
        if checkpoint_key.endswith(str(first_operation.id)):
            return SimpleNamespace(
                payload={
                    "id": str(first_operation.id),
                    "status": "conflict_skipped",
                }
            )
        return None

    async def save_checkpoint(_repository, *_args, **kwargs):
        return SimpleNamespace(payload=kwargs["payload"])

    async def current_target_version(_repository, _task_id):
        return parent

    async def execute(_service, **kwargs):
        operation = kwargs["operations"][0]
        executed.append(operation.id)
        result = SimpleNamespace(
            operation_id=operation.id,
            status="succeeded",
        )
        return SimpleNamespace(
            output_target_version=None,
            by_operation={operation.id: result},
        )

    monkeypatch.setattr(AgentRuntimeRepository, "get_checkpoint", get_checkpoint)
    monkeypatch.setattr(AgentRuntimeRepository, "save_checkpoint", save_checkpoint)
    monkeypatch.setattr(
        ExecutionRepository,
        "current_target_version",
        current_target_version,
    )
    monkeypatch.setattr(AgentExecutionService, "execute", execute)

    fact = await CsvRollbackHandlers(output_root=tmp_path).execute_operation(
        _Session(task, parent),  # type: ignore[arg-type]
        _context(task_id),
        second_operation.id,
    )

    assert fact["status"] == "succeeded"
    assert executed == [second_operation.id]


@pytest.mark.asyncio
async def test_execute_operation_uses_latest_parent_and_preserves_unrelated_fields(
    monkeypatch,
    tmp_path,
) -> None:
    task_id = uuid4()
    mutation = {
        "id": str(uuid4()),
        "operation": "update",
        "entity_kind": "student",
        "target_source_identifier": "student-1",
        "before": {"phone": "A"},
        "after": {"phone": "B"},
    }
    current_path = tmp_path / "advanced.csv"
    current_path.write_text(
        "id,手机号,班级\nstudent-1,B,二班\n",
        encoding="utf-8",
    )
    original = SimpleNamespace(
        id=uuid4(),
        file_sha256="a" * 64,
        task_id=uuid4(),
        storage_path=str(tmp_path / "original.csv"),
    )
    current = SimpleNamespace(
        id=uuid4(),
        file_sha256="b" * 64,
        task_id=original.task_id,
        storage_path=str(current_path),
    )
    planned = compare_csv_rollback_mutation(
        mutation,
        current={"source_id": "student-1", "phone": "B", "class_name": "一班"},
    )
    task = SimpleNamespace(
        id=task_id,
        request_hash="request-hash",
        parent_task_id=uuid4(),
        agent_intent={
            "target_version_id": str(original.id),
            "operations": [mutation],
            "restore_comparisons": [planned],
        },
    )
    observed = {}

    async def get_checkpoint(_repository, *_args, **_kwargs):
        return None

    async def save_checkpoint(_repository, *_args, **kwargs):
        return SimpleNamespace(payload=kwargs["payload"])

    async def current_target_version(_repository, _task_id):
        return current

    async def execute(_service, **kwargs):
        operation = kwargs["operations"][0]
        observed["target_version"] = kwargs["target_version"]
        observed["before"] = operation.before
        observed["after"] = operation.after
        observed["parent"] = kwargs["target"].parent
        result = SimpleNamespace(
            operation_id=operation.id,
            status="succeeded",
        )
        return SimpleNamespace(
            output_target_version=None,
            by_operation={operation.id: result},
        )

    monkeypatch.setattr(AgentRuntimeRepository, "get_checkpoint", get_checkpoint)
    monkeypatch.setattr(AgentRuntimeRepository, "save_checkpoint", save_checkpoint)
    monkeypatch.setattr(
        ExecutionRepository,
        "current_target_version",
        current_target_version,
    )
    monkeypatch.setattr(AgentExecutionService, "execute", execute)

    operation = _rollback_operation(
        mutation,
        target_version=f"sha256:{original.file_sha256}",
    )
    fact = await CsvRollbackHandlers(output_root=tmp_path).execute_operation(
        _Session(task, original),  # type: ignore[arg-type]
        _context(task_id),
        operation.id,
    )

    assert fact["status"] == "succeeded"
    assert observed == {
        "target_version": f"sha256:{current.file_sha256}",
        "before": {"phone": "B"},
        "after": {"phone": "A"},
        "parent": current,
    }
