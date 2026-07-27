from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.agent_graph.repository import AgentGraphRepository
from app.agent_reporting.service import AgentReportingService
from app.agent_runtime.service import AgentSupervisorService
from app.agent_runtime.state_machine import AgentPhase
from app.api.routes.agent import get_agent_graph_progress
from app.core.security import OperatorContext
from app.models.agent_runtime import SchoolTaskLockRecord
from app.models.reconciliation import ReconciliationTask


@pytest.mark.asyncio
async def test_graph_rollback_is_an_independent_locked_graph_run(session) -> None:
    source = ReconciliationTask(
        tenant_id="school-graph-rollback",
        scope_id="all",
        snapshot_mode="full",
        entity_types=["student"],
        status="completed",
        stage="terminal",
        workflow_version="agent-graph-v1",
        task_kind="sync",
        idempotency_key=str(uuid4()),
        request_hash=str(uuid4()),
    )
    session.add(source)
    await session.flush()
    mutation_id = uuid4()
    await AgentReportingService(session).generate(
        task_id=source.id,
        tenant_id=source.tenant_id,
        kind="sync",
        terminal_state="completed",
        facts={
            "mutations": [
                {
                    "id": str(mutation_id),
                    "status": "succeeded",
                    "verification": {"valid": True},
                    "operation": "update",
                    "entity_kind": "student",
                    "target_source_identifier": "csv:2",
                    "before": {"name": "旧姓名"},
                    "after": {"name": "新姓名"},
                }
            ]
        },
    )
    target_version_id = uuid4()
    preview = await AgentReportingService(session).create_rollback_task(
        source_task_id=source.id,
        tenant_id=source.tenant_id,
        requested_by="demo-operator",
        target_version_id=target_version_id,
    )
    rollback = await session.get(ReconciliationTask, preview.task_id)
    assert rollback is not None
    assert rollback.id != source.id
    assert rollback.workflow_version == "agent-graph-v1"
    run = await AgentSupervisorService(
        session,
        operator=OperatorContext(
            operator_id="demo-operator",
            tenant_id=source.tenant_id,
        ),
    ).confirm_rollback(task_id=rollback.id)

    graph = await AgentGraphRepository(session).get_run_state_for_agent_run(run.id)
    assert graph is not None
    assert graph.graph_version == "agent-rollback-graph-v1"
    assert graph.current_node == "load_verified_mutations"
    lock = await session.scalar(
        select(SchoolTaskLockRecord).where(
            SchoolTaskLockRecord.owner_run_id == run.id,
            SchoolTaskLockRecord.active.is_(True),
        )
    )
    assert lock is not None

    graph.cursor = 3
    await session.flush()
    await AgentGraphRepository(session).record_human_gate(
        graph_run_id=graph.id,
        cursor=3,
        gate_kind="rollback_approval",
        member_ids=(str(mutation_id),),
        content_hash=f"sha256:{'a' * 64}",
        status="pending",
    )
    graph.current_node = "wait_rollback_approval"
    graph.cursor = 4
    run.phase = AgentPhase.APPROVE_RESTORE.value
    run.status = "waiting_human"
    await session.flush()
    progress = await get_agent_graph_progress(
        rollback.id,
        SimpleNamespace(
            app=SimpleNamespace(
                state=SimpleNamespace(
                    settings=SimpleNamespace(new_agent_enabled=True)
                )
            )
        ),
        session,
        OperatorContext(
            operator_id="demo-operator",
            tenant_id=source.tenant_id,
        ),
    )

    approval = next(
        gate for gate in progress.human_gates if gate.kind == "rollback_approval"
    )
    assert approval.item_count == 1
    assert len(approval.items) == 1
    assert approval.items[0].finding_id == mutation_id
    assert approval.items[0].operation_zh == "恢复同步修改的学生记录"
    assert approval.items[0].source_locator == "csv:2"
    assert [
        (change.field_zh, change.before, change.after)
        for change in approval.items[0].changes
    ] == [("姓名", "新姓名", "旧姓名")]
