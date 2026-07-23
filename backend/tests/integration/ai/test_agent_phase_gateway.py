from uuid import uuid4

import pytest

from app.ai.mcp.agent_authorization import (
    AgentCapability,
    AgentToolAuthorizationError,
    AgentToolContext,
)
from app.ai.mcp.agent_gateway import AgentPhaseToolGateway
from app.ai.mcp.server import create_agent_fastmcp_server
from app.core.security import OperatorContext
from app.models.agent_runtime import AgentRunRecord
from app.models.reconciliation import ReconciliationTask


@pytest.mark.asyncio
async def test_agent_phase_gateway_binds_tool_to_durable_tenant_task_and_phase(session) -> None:
    task = ReconciliationTask(
        tenant_id="school-1",
        scope_id="all",
        snapshot_mode="full",
        entity_types=["student"],
        status="running",
        stage="analysis",
        workflow_version="new-agent-v1",
        task_kind="sync",
        idempotency_key=f"gateway-{uuid4()}",
        request_hash="a" * 64,
    )
    session.add(task)
    await session.flush()
    run = AgentRunRecord(
        task_id=task.id,
        tenant_id=task.tenant_id,
        kind="sync",
        phase="analyze_batches",
        status="running",
    )
    session.add(run)
    await session.flush()
    resource_id = uuid4()
    context = AgentToolContext(
        operator_id="demo-operator",
        tenant_id=task.tenant_id,
        task_id=task.id,
        run_id=run.id,
        phase="analyze_batches",
        allowed_capabilities=frozenset({AgentCapability.READ_IDENTITY_EVIDENCE}),
        allowed_resource_ids=frozenset({resource_id}),
        allowed_connector_ids=frozenset({"authority"}),
    )

    async def read_evidence(_context, arguments):
        return {"item_id": arguments["item_id"]}

    gateway = AgentPhaseToolGateway(
        session,
        operator=OperatorContext(operator_id="demo-operator", tenant_id="school-1"),
        tools={AgentCapability.READ_IDENTITY_EVIDENCE: read_evidence},
    )

    assert await gateway.call(
        AgentCapability.READ_IDENTITY_EVIDENCE,
        context=context,
        resource_id=resource_id,
        connector_id="authority",
        arguments={"item_id": str(resource_id)},
    ) == {"item_id": str(resource_id)}

    with pytest.raises(AgentToolAuthorizationError):
        await gateway.call(
            AgentCapability.READ_IDENTITY_EVIDENCE,
            context=context.model_copy(update={"tenant_id": "other-school"}),
            resource_id=resource_id,
            connector_id="authority",
            arguments={"item_id": str(resource_id)},
        )
    with pytest.raises(AgentToolAuthorizationError, match="arbitrary"):
        await gateway.call(
            AgentCapability.READ_IDENTITY_EVIDENCE,
            context=context,
            resource_id=resource_id,
            connector_id="authority",
            arguments={"sql": "select * from people"},
        )
    with pytest.raises(AgentToolAuthorizationError, match="connector"):
        await gateway.call(
            AgentCapability.READ_IDENTITY_EVIDENCE,
            context=context,
            connector_id="invented",
            arguments={"item_id": str(resource_id)},
        )


@pytest.mark.asyncio
async def test_agent_fastmcp_registers_every_phase_capability_without_legacy_tools(
    session,
) -> None:
    context = AgentToolContext(
        operator_id="demo-operator",
        tenant_id="school-1",
        task_id=uuid4(),
        run_id=uuid4(),
        phase="analyze_batches",
    )
    gateway = AgentPhaseToolGateway(
        session,
        operator=OperatorContext(operator_id="demo-operator", tenant_id="school-1"),
        tools={},
    )

    server = create_agent_fastmcp_server(gateway, lambda _ctx: context)

    assert {tool.name for tool in await server.list_tools()} == {
        capability.value for capability in AgentCapability
    }
    assert "difference_context" not in {tool.name for tool in await server.list_tools()}
