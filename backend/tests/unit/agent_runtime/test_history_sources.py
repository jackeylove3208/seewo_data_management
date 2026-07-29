from uuid import UUID, uuid4

from app.agent_runtime.history_sources import resolve_history_target_sources
from app.models.reconciliation import ReconciliationTask


def _task(
    *,
    target: dict[str, object] | None,
    task_kind: str = "sync",
    parent_task_id: UUID | None = None,
) -> ReconciliationTask:
    return ReconciliationTask(
        id=uuid4(),
        tenant_id="school-1",
        scope_id="all",
        snapshot_mode="full",
        entity_types=["student"],
        status="completed",
        stage="terminal",
        workflow_version="agent-graph-v1",
        task_kind=task_kind,
        parent_task_id=parent_task_id,
        title="历史任务",
        agent_intent={"target": target} if target is not None else None,
        idempotency_key=str(uuid4()),
        request_hash="a" * 64,
    )


def test_same_authorized_local_target_has_one_stable_group() -> None:
    first = _task(target={"kind": "local", "source_ref": "seewo/current.csv"})
    second = _task(target={"kind": "local", "source_ref": "seewo/./current.csv"})

    resolved = resolve_history_target_sources((first, second), upload_names={})

    assert resolved[first.id].key == resolved[second.id].key
    assert resolved[first.id].name == "current.csv"
    assert resolved[first.id].kind == "local"
    assert resolved[first.id].identified is True
    assert "seewo/current.csv" not in resolved[first.id].key


def test_same_named_temporary_uploads_remain_different_groups() -> None:
    first_upload_id = uuid4()
    second_upload_id = uuid4()
    first = _task(target={"kind": "csv", "upload_id": str(first_upload_id)})
    second = _task(target={"kind": "csv", "upload_id": str(second_upload_id)})

    resolved = resolve_history_target_sources(
        (first, second),
        upload_names={
            first_upload_id: "students.csv",
            second_upload_id: "students.csv",
        },
    )

    assert resolved[first.id].key != resolved[second.id].key
    assert resolved[first.id].name == "临时上传 · students.csv"
    assert resolved[second.id].name == "临时上传 · students.csv"
    assert first_upload_id.hex not in resolved[first.id].key


def test_rollback_inherits_parent_sync_target_group() -> None:
    source = _task(
        target={"kind": "database", "configuration_id": "seewo-mysql"}
    )
    rollback = _task(
        target={"kind": "database", "configuration_id": "wrong-rollback-target"},
        task_kind="rollback",
        parent_task_id=source.id,
    )

    resolved = resolve_history_target_sources((rollback, source), upload_names={})

    assert resolved[rollback.id] == resolved[source.id]
    assert resolved[rollback.id].name == "seewo-mysql"
    assert resolved[rollback.id].kind == "database"


def test_unresolvable_legacy_task_uses_shared_unknown_group() -> None:
    missing_target = _task(target=None)
    missing_parent = _task(
        target={"kind": "local", "source_ref": "seewo/should-not-be-used.csv"},
        task_kind="rollback",
        parent_task_id=uuid4(),
    )

    resolved = resolve_history_target_sources(
        (missing_target, missing_parent),
        upload_names={},
    )

    assert resolved[missing_target.id] == resolved[missing_parent.id]
    assert resolved[missing_target.id].name == "其他历史任务"
    assert resolved[missing_target.id].kind == "unknown"
    assert resolved[missing_target.id].identified is False


def test_untrusted_target_identifiers_are_not_exposed_as_display_names() -> None:
    database = _task(
        target={
            "kind": "database",
            "configuration_id": "mysql://operator:secret@private.example/reconcile",
        }
    )
    windows_path = _task(
        target={
            "kind": "local",
            "source_ref": r"C:\private\school\seewo.csv",
        }
    )

    resolved = resolve_history_target_sources(
        (database, windows_path),
        upload_names={},
    )

    assert resolved[database.id].name == "希沃数据库"
    assert "secret" not in resolved[database.id].name
    assert resolved[windows_path.id].name == "其他历史任务"
    assert "private" not in resolved[windows_path.id].name


def test_long_safe_names_cannot_break_the_history_response() -> None:
    local = _task(
        target={
            "kind": "local",
            "source_ref": f"seewo/{'x' * 300}.csv",
        }
    )
    upload_id = uuid4()
    upload = _task(target={"kind": "csv", "upload_id": str(upload_id)})

    resolved = resolve_history_target_sources(
        (local, upload),
        upload_names={upload_id: f"{'y' * 255}.csv"},
    )

    assert resolved[local.id].identified is True
    assert resolved[upload.id].identified is True
    assert len(resolved[local.id].name) <= 255
    assert len(resolved[upload.id].name) <= 255
