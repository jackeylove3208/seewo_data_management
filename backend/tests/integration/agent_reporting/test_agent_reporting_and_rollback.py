from uuid import uuid4

import pytest

from app.agent_reporting.service import AgentReportingService
from app.models.reconciliation import ReconciliationTask


def _task(tenant_id: str = "school-1", *, kind: str = "sync") -> ReconciliationTask:
    return ReconciliationTask(
        id=uuid4(),
        tenant_id=tenant_id,
        scope_id="all",
        snapshot_mode="full",
        entity_types=["student"],
        status="completed",
        stage="terminal",
        workflow_version="new-agent-v1",
        task_kind=kind,
        idempotency_key=str(uuid4()),
        request_hash="a" * 64,
    )


@pytest.mark.asyncio
async def test_agent_report_persists_terminal_facts_and_keeps_narrative_out_of_rollback_evidence(
    session,
) -> None:
    task = _task()
    session.add(task)
    await session.flush()

    report = await AgentReportingService(session).generate(
        task_id=task.id,
        tenant_id=task.tenant_id,
        kind="sync",
        terminal_state="partial",
        facts={
            "invalid_rows": [{"source": "authority", "row": 3}],
            "excluded_findings": [{"id": "finding-1", "reason": "rejected"}],
            "mutations": [
                {"id": "op-1", "status": "succeeded", "verification": {"valid": True}},
                {"id": "op-2", "status": "failed", "verification": {"valid": False}},
            ],
        },
        narrative={"summary": "建议回滚 op-1"},
    )

    assert report.facts["mutation_summary"] == {
        "succeeded": 1,
        "failed": 1,
        "blocked": 0,
        "rejected": 0,
        "skipped": 0,
    }
    assert report.facts["rollback_evidence"]["successful_mutation_ids"] == ["op-1"]
    assert report.facts["rollback_evidence"]["eligible"] is True
    assert report.content["narrative"]["summary"] == "建议回滚 op-1"


@pytest.mark.asyncio
async def test_agent_report_marks_abnormal_input_ineligible_for_rollback(session) -> None:
    task = _task()
    session.add(task)
    await session.flush()

    report = await AgentReportingService(session).generate(
        task_id=task.id,
        tenant_id=task.tenant_id,
        kind="sync",
        terminal_state="abnormal_input",
        facts={
            "mutations": [
                {"id": "op-1", "status": "succeeded", "verification": {"valid": True}}
            ]
        },
    )

    assert report.facts["rollback_evidence"]["eligible"] is False
    assert report.facts["rollback_evidence"]["reason"] == "abnormal_input"


@pytest.mark.asyncio
async def test_agent_history_is_tenant_scoped_and_deletion_uses_verified_mutations(session) -> None:
    first = _task()
    second = _task("school-2")
    session.add_all([first, second])
    await session.flush()
    service = AgentReportingService(session)
    await service.generate(
        task_id=first.id,
        tenant_id=first.tenant_id,
        kind="sync",
        terminal_state="completed",
        facts={"mutations": []},
    )
    await service.generate(
        task_id=second.id,
        tenant_id=second.tenant_id,
        kind="sync",
        terminal_state="completed",
        facts={
            "mutations": [
                {"id": "op-1", "status": "succeeded", "verification": {"valid": True}}
            ]
        },
    )

    history = await service.history(tenant_id="school-1", limit=10)
    assert [item.task_id for item in history.items] == [first.id]
    assert history.items[0].deletion_eligible is True


@pytest.mark.asyncio
async def test_rollback_is_a_new_task_and_is_blocked_by_another_active_school_lock(session) -> None:
    original = _task()
    session.add(original)
    await session.flush()
    service = AgentReportingService(session)
    await service.generate(
        task_id=original.id,
        tenant_id=original.tenant_id,
        kind="sync",
        terminal_state="completed",
        facts={
            "mutations": [
                {"id": "op-1", "status": "succeeded", "verification": {"valid": True}}
            ]
        },
    )

    preview = await service.create_rollback_task(
        source_task_id=original.id,
        tenant_id=original.tenant_id,
        requested_by="operator-1",
        target_version_id=uuid4(),
    )

    assert preview.task_id != original.id
    assert preview.task_kind == "rollback"
    assert preview.report_id is None
    assert [operation["compensation_for"] for operation in preview.operations] == ["op-1"]

    replay = await service.create_rollback_task(
        source_task_id=original.id,
        tenant_id=original.tenant_id,
        requested_by="operator-1",
        target_version_id=preview.target_version_id,
    )
    assert replay.task_id == preview.task_id
