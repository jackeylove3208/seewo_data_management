from uuid import uuid4

import pytest

from app.ai.mcp.agent_authorization import (
    AgentCapability,
    AgentToolAuthorizationError,
    AgentToolContext,
    require_agent_capability,
    require_agent_resource,
)


def agent_context(**overrides) -> AgentToolContext:
    values = {
        "operator_id": "demo-operator",
        "tenant_id": "school-1",
        "conversation_id": uuid4(),
        "task_id": uuid4(),
        "run_id": uuid4(),
        "phase": "analyze_batches",
        "snapshot_ids": frozenset({uuid4(), uuid4()}),
        "allowed_capabilities": frozenset({AgentCapability.READ_IDENTITY_EVIDENCE}),
        "allowed_resource_ids": frozenset({uuid4()}),
    }
    values.update(overrides)
    return AgentToolContext.model_validate(values)


def test_phase_capability_must_be_server_authorized() -> None:
    context = agent_context()

    require_agent_capability(context, AgentCapability.READ_IDENTITY_EVIDENCE)

    with pytest.raises(AgentToolAuthorizationError, match="capability not authorized"):
        require_agent_capability(context, AgentCapability.EXECUTE_TARGET_OPERATION)


def test_model_invented_resource_id_is_rejected() -> None:
    context = agent_context()

    with pytest.raises(AgentToolAuthorizationError, match="resource not authorized"):
        require_agent_resource(context, uuid4())
