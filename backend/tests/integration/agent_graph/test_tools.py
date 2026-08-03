from uuid import uuid4

import pytest
from sqlalchemy import select

from app.agent_graph.evidence import build_evidence_manifest
from app.agent_graph.repository import AgentGraphRepository
from app.agent_graph.tools import (
    GraphPhaseToolGateway,
    GraphToolArgumentRejected,
    GraphToolAuthorizationError,
    GraphToolContext,
    GraphToolExecutionError,
    GraphToolReplayConflict,
)
from app.agent_runtime.repository import AgentRuntimeRepository
from app.agent_runtime.state_machine import AgentPhase, AgentRunKind
from app.core.security import OperatorContext
from app.models.agent_graph import AgentToolCallRecord
from app.models.reconciliation import ReconciliationTask


async def _tool_fixture(session):
    task = ReconciliationTask(
        tenant_id="school-1",
        scope_id="all",
        snapshot_mode="full",
        entity_types=["student"],
        status="running",
        stage="analysis",
        workflow_version="agent-graph-v1",
        idempotency_key=str(uuid4()),
        request_hash="request-hash",
    )
    session.add(task)
    await session.flush()
    run = await AgentRuntimeRepository(session).create_run(
        task_id=task.id,
        tenant_id=task.tenant_id,
        conversation_id=None,
        kind=AgentRunKind.SYNC,
        workflow_version="agent-graph-v1",
    )
    run.phase = AgentPhase.ANALYZE_BATCHES.value
    state = await AgentGraphRepository(session).create_run_state(
        run_id=run.id,
        graph_version="agent-sync-graph-v1",
        initial_node="analyze_actionable_batches",
    )
    manifest = build_evidence_manifest(
        tenant_ref=f"tenant-ref:{state.id}",
        task_id=str(task.id),
        run_id=str(run.id),
        graph_node=state.current_node,
        action_id="analyze_students:batch-1",
        resource_ids=("work-item:1",),
        allowed_evidence_refs=("paired-record:1",),
        issued_sensitive_tokens=("phone-token:1",),
    )
    manifest_record = await AgentGraphRepository(session).record_manifest(
        graph_run_id=state.id,
        cursor=state.cursor,
        graph_node=state.current_node,
        action_id=manifest.action_id,
        manifest=manifest.model_dump(mode="json"),
        content_hash=manifest.content_hash,
        record_id=manifest.manifest_id,
    )
    invocation = await AgentGraphRepository(session).record_invocation(
        graph_run_id=state.id,
        cursor=state.cursor,
        action_id=manifest.action_id,
        evidence_manifest_id=manifest_record.id,
        execution_mode="skill_model",
        skill_name="reconcile-entity-batch",
        skill_version="1.0.0",
        schema_version="agent-finding-v1",
        attempt=1,
        status="running",
        input_hash="sha256:" + ("2" * 64),
        output_hash="sha256:" + ("3" * 64),
        model_provenance={},
    )
    context = GraphToolContext(
        operator_id="demo-operator",
        tenant_id=task.tenant_id,
        task_id=task.id,
        run_id=run.id,
        graph_run_id=state.id,
        graph_node=state.current_node,
        graph_cursor=state.cursor,
        action_id=manifest.action_id,
        evidence_manifest_id=manifest_record.id,
        invocation_id=invocation.id,
        allowed_tools=frozenset({"read_work_item", "read_paired_record_evidence"}),
    )
    return context


@pytest.mark.asyncio
async def test_graph_tool_reads_only_manifest_members_and_records_audit(session) -> None:
    context = await _tool_fixture(session)

    async def read_work_item(_context, arguments):
        return {"resource_id": arguments["resource_id"], "safe": True}

    gateway = GraphPhaseToolGateway(
        session,
        operator=OperatorContext(operator_id="demo-operator", tenant_id="school-1"),
        tools={"read_work_item": read_work_item},
    )
    result = await gateway.call(
        "read_work_item",
        context=context,
        arguments={"resource_id": "work-item:1"},
        resource_id="work-item:1",
        model_turn=1,
    )

    assert result.payload == {"resource_id": "work-item:1", "safe": True}
    records = tuple(await session.scalars(select(AgentToolCallRecord)))
    assert len(records) == 1
    assert records[0].authorized is True
    assert records[0].tool_name == "read_work_item"
    assert records[0].model_turn == 1
    assert records[0].replay_descriptor == {"resource_id": "work-item:1"}


@pytest.mark.asyncio
async def test_graph_tool_without_model_turn_does_not_persist_replay_arguments(
    session,
) -> None:
    context = await _tool_fixture(session)

    async def read_work_item(_context, arguments):
        return {"resource_id": arguments["resource_id"], "safe": True}

    gateway = GraphPhaseToolGateway(
        session,
        operator=OperatorContext(operator_id="demo-operator", tenant_id="school-1"),
        tools={"read_work_item": read_work_item},
    )
    await gateway.call(
        "read_work_item",
        context=context,
        arguments={"resource_id": "work-item:1"},
        resource_id="work-item:1",
    )

    record = await session.scalar(select(AgentToolCallRecord))
    assert record is not None
    assert record.model_turn is None
    assert record.replay_descriptor is None


@pytest.mark.asyncio
async def test_graph_tool_replay_reauthorizes_without_duplicate_audit(session) -> None:
    context = await _tool_fixture(session)

    async def read_work_item(_context, arguments):
        return {"resource_id": arguments["resource_id"], "safe": True}

    gateway = GraphPhaseToolGateway(
        session,
        operator=OperatorContext(operator_id="demo-operator", tenant_id="school-1"),
        tools={"read_work_item": read_work_item},
    )
    original = await gateway.call(
        "read_work_item",
        context=context,
        arguments={"resource_id": "work-item:1"},
        resource_id="work-item:1",
        model_turn=1,
    )
    record = await session.scalar(select(AgentToolCallRecord))
    assert record is not None

    replayed = await gateway.replay(
        "read_work_item",
        context=context,
        arguments={"resource_id": "work-item:1"},
        expected_result_hash=record.result_hash,
    )

    assert replayed.payload == original.payload
    records = tuple(await session.scalars(select(AgentToolCallRecord)))
    assert len(records) == 1


@pytest.mark.asyncio
async def test_graph_tool_replay_rejects_changed_result(session) -> None:
    context = await _tool_fixture(session)
    current_payload = {"resource_id": "work-item:1", "version": 1}

    async def read_work_item(_context, _arguments):
        return current_payload

    gateway = GraphPhaseToolGateway(
        session,
        operator=OperatorContext(operator_id="demo-operator", tenant_id="school-1"),
        tools={"read_work_item": read_work_item},
    )
    await gateway.call(
        "read_work_item",
        context=context,
        arguments={"resource_id": "work-item:1"},
        resource_id="work-item:1",
        model_turn=1,
    )
    record = await session.scalar(select(AgentToolCallRecord))
    assert record is not None
    current_payload["version"] = 2

    with pytest.raises(GraphToolReplayConflict, match="changed"):
        await gateway.replay(
            "read_work_item",
            context=context,
            arguments={"resource_id": "work-item:1"},
            expected_result_hash=record.result_hash,
        )


@pytest.mark.asyncio
async def test_graph_tool_rejects_resource_outside_manifest_and_audits_denial(session) -> None:
    context = await _tool_fixture(session)
    gateway = GraphPhaseToolGateway(
        session,
        operator=OperatorContext(operator_id="demo-operator", tenant_id="school-1"),
        tools={"read_work_item": lambda _context, _arguments: None},
    )

    with pytest.raises(GraphToolArgumentRejected, match="evidence membership"):
        await gateway.call(
            "read_work_item",
            context=context,
            arguments={"resource_id": "work-item:foreign"},
            resource_id="work-item:foreign",
        )

    record = await session.scalar(select(AgentToolCallRecord))
    assert record is not None
    assert record.authorized is False
    assert record.status == "denied"


@pytest.mark.asyncio
async def test_analysis_node_cannot_request_execution_or_arbitrary_infrastructure(
    session,
) -> None:
    context = await _tool_fixture(session)
    gateway = GraphPhaseToolGateway(
        session,
        operator=OperatorContext(operator_id="demo-operator", tenant_id="school-1"),
        tools={},
    )

    with pytest.raises(GraphToolAuthorizationError, match="phase"):
        await gateway.call(
            "request_operation_execution",
            context=context.model_copy(
                update={"allowed_tools": frozenset({"request_operation_execution"})}
            ),
            arguments={"operation_id": "operation:1"},
            resource_id="work-item:1",
        )
    with pytest.raises(GraphToolAuthorizationError, match="arbitrary"):
        await gateway.call(
            "read_work_item",
            context=context,
            arguments={"sql": "select * from students"},
            resource_id="work-item:1",
        )


@pytest.mark.asyncio
async def test_graph_tool_rejects_tenant_override(session) -> None:
    context = await _tool_fixture(session)
    gateway = GraphPhaseToolGateway(
        session,
        operator=OperatorContext(operator_id="demo-operator", tenant_id="school-1"),
        tools={},
    )

    with pytest.raises(GraphToolAuthorizationError, match="operator"):
        await gateway.call(
            "read_work_item",
            context=context.model_copy(update={"tenant_id": "other-school"}),
            arguments={"resource_id": "work-item:1"},
            resource_id="work-item:1",
        )


@pytest.mark.asyncio
async def test_authorized_tool_failure_is_audited_without_leaking_error(session) -> None:
    context = await _tool_fixture(session)

    async def failing_tool(_context, _arguments):
        raise RuntimeError("sensitive connector detail")

    gateway = GraphPhaseToolGateway(
        session,
        operator=OperatorContext(operator_id="demo-operator", tenant_id="school-1"),
        tools={"read_work_item": failing_tool},
    )

    with pytest.raises(GraphToolExecutionError, match="failed safely"):
        await gateway.call(
            "read_work_item",
            context=context,
            arguments={"resource_id": "work-item:1"},
            resource_id="work-item:1",
        )

    record = await session.scalar(select(AgentToolCallRecord))
    assert record is not None
    assert record.authorized is True
    assert record.status == "failed"
