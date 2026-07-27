from uuid import uuid4

import pytest

from app.agent_graph.evidence import build_evidence_manifest
from app.ai.graph_subagents import _tool_arguments_schema


@pytest.mark.parametrize(
    "tool_name",
    ("read_execution_plan", "read_ready_operations", "request_execution_batch"),
)
def test_plan_tools_only_accept_the_frozen_execution_plan_resource(
    tool_name: str,
) -> None:
    plan_id = uuid4()
    operation_id = uuid4()
    manifest = build_evidence_manifest(
        tenant_ref="tenant-ref:test",
        task_id=str(uuid4()),
        run_id=str(uuid4()),
        graph_node="execute_ready_operations",
        action_id="execute_ready_operations",
        resource_ids=(
            f"execution-plan:{plan_id}",
            f"operation:{operation_id}",
        ),
        allowed_evidence_refs=(f"execution-outcome:{operation_id}",),
    )

    schema = _tool_arguments_schema(tool_name, manifest=manifest)

    assert schema["required"] == ["resource_id"]
    assert schema["properties"] == {
        "resource_id": {
            "type": "string",
            "enum": [f"execution-plan:{plan_id}"],
        }
    }


@pytest.mark.parametrize(
    "tool_name",
    ("request_operation_execution", "read_operation_verification"),
)
def test_operation_tools_only_accept_frozen_operation_resources(
    tool_name: str,
) -> None:
    plan_id = uuid4()
    operation_ids = (uuid4(), uuid4())
    manifest = build_evidence_manifest(
        tenant_ref="tenant-ref:test",
        task_id=str(uuid4()),
        run_id=str(uuid4()),
        graph_node="execute_ready_operations",
        action_id="execute_ready_operations",
        resource_ids=(
            f"execution-plan:{plan_id}",
            *(f"operation:{item}" for item in operation_ids),
        ),
        allowed_evidence_refs=tuple(
            f"execution-outcome:{item}" for item in operation_ids
        ),
    )

    schema = _tool_arguments_schema(tool_name, manifest=manifest)

    assert schema["required"] == ["resource_id"]
    assert schema["properties"] == {
        "resource_id": {
            "type": "string",
            "enum": [f"operation:{item}" for item in operation_ids],
        }
    }
