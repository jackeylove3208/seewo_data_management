from uuid import uuid4

import pytest
from sqlalchemy import select

from app.agent_graph.evidence import build_evidence_manifest
from app.agent_graph.governance_executors import (
    FrozenApprovalDraft,
    GraphExecutionTools,
    GraphGovernanceExecutionExecutor,
    GraphHumanGateService,
)
from app.agent_graph.repository import AgentGraphRepository
from app.agent_graph.tools import GraphPhaseToolGateway
from app.agent_runtime.repository import AgentRuntimeRepository
from app.agent_runtime.state_machine import AgentRunKind
from app.ai.graph_subagents import GraphSkillInvocation, GraphSkillModelRunner
from app.ai.providers.base import LLMRequest, LLMResponse
from app.ai.skills.contracts import OperationOutcome
from app.core.security import OperatorContext
from app.models.agent_graph import AgentHumanGateRecord
from app.models.reconciliation import ReconciliationTask


@pytest.mark.asyncio
async def test_phone_risk_group_waits_once_for_homogeneous_batch(session) -> None:
    task = ReconciliationTask(
        tenant_id="school-human-gate",
        scope_id="all",
        snapshot_mode="full",
        entity_types=["student"],
        status="running",
        stage="governance",
        workflow_version="agent-graph-v1",
        idempotency_key=str(uuid4()),
        request_hash=str(uuid4()),
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
    graph = await AgentGraphRepository(session).create_run_state(
        run_id=run.id,
        graph_version="agent-sync-graph-v1",
        initial_node="wait_high_risk_approvals",
    )
    finding_ids = tuple(uuid4() for _index in range(50))
    draft = FrozenApprovalDraft(
        group_key="field_difference:student:update:phone",
        finding_ids=finding_ids,
        issue_kind="field_difference",
        entity_kind="student",
        operation="update",
        risk="high",
        policy_version="agent-risk-v1",
    )

    first = await GraphHumanGateService(session).freeze_high_risk_approvals(
        graph_run_id=graph.id,
        cursor=graph.cursor,
        groups=(draft,),
    )
    replay = await GraphHumanGateService(session).freeze_high_risk_approvals(
        graph_run_id=graph.id,
        cursor=graph.cursor,
        groups=(draft,),
    )

    assert first[0].id == replay[0].id
    assert first[0].member_ids == [str(item) for item in finding_ids]
    gates = tuple(await session.scalars(select(AgentHumanGateRecord)))
    assert len(gates) == 1


class ExecutionProvider:
    def __init__(self, outputs):
        self.outputs = list(outputs)

    async def complete_json_once(self, _request: LLMRequest) -> LLMResponse:
        return LLMResponse(
            output=self.outputs.pop(0),
            provider="scripted",
            model="execution-model",
            request_id=str(uuid4()),
        )


@pytest.mark.asyncio
async def test_execution_continues_independent_operation_after_failure(session) -> None:
    task = ReconciliationTask(
        tenant_id="school-graph-execution",
        scope_id="all",
        snapshot_mode="full",
        entity_types=["student"],
        status="running",
        stage="governance",
        workflow_version="agent-graph-v1",
        idempotency_key=str(uuid4()),
        request_hash=str(uuid4()),
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
    graph = await AgentGraphRepository(session).create_run_state(
        run_id=run.id,
        graph_version="agent-sync-graph-v1",
        initial_node="execute_ready_operations",
    )
    plan_id = uuid4()
    failed_id = uuid4()
    independent_id = uuid4()
    action_id = "execute_ready_operations"
    resources = (
        f"execution-plan:{plan_id}",
        f"operation:{failed_id}",
        f"operation:{independent_id}",
    )
    manifest = build_evidence_manifest(
        tenant_ref=f"tenant-ref:{graph.id}",
        task_id=str(task.id),
        run_id=str(run.id),
        graph_node=graph.current_node,
        action_id=action_id,
        resource_ids=resources,
        allowed_evidence_refs=("execution-outcomes:v1",),
    )
    manifest_record = await AgentGraphRepository(session).record_manifest(
        graph_run_id=graph.id,
        cursor=graph.cursor,
        graph_node=graph.current_node,
        action_id=action_id,
        manifest=manifest.model_dump(mode="json"),
        content_hash=manifest.content_hash,
        record_id=manifest.manifest_id,
    )

    async def execute(operation_id):
        if operation_id == failed_id:
            return OperationOutcome(
                operation_id=operation_id,
                status="failed",
                safe_error_code="target_write_failed",
            )
        return OperationOutcome(
            operation_id=operation_id,
            status="succeeded",
            verification_ref=f"verification:{operation_id}",
        )

    tools = GraphExecutionTools(
        task_id=task.id,
        run_id=run.id,
        tenant_id=task.tenant_id,
        plan_id=plan_id,
        operation_ids=(failed_id, independent_id),
        execute_operation=execute,
    )
    provider = ExecutionProvider(
        [
            {
                "result": {
                    "tool_call": {
                        "name": "request_operation_execution",
                        "arguments": {"resource_id": f"operation:{failed_id}"},
                    }
                }
            },
            {
                "result": {
                    "tool_call": {
                        "name": "request_operation_execution",
                        "arguments": {"resource_id": f"operation:{independent_id}"},
                    }
                }
            },
            {
                "result": {
                    "schema_version": "agent-contract-v1",
                    "outcomes": [
                        {
                            "operation_id": str(failed_id),
                            "status": "failed",
                            "verification_ref": None,
                            "safe_error_code": "target_write_failed",
                        },
                        {
                            "operation_id": str(independent_id),
                            "status": "succeeded",
                            "verification_ref": f"verification:{independent_id}",
                            "safe_error_code": None,
                        },
                    ],
                }
            },
        ]
    )
    operator = OperatorContext(
        operator_id="demo-operator",
        tenant_id=task.tenant_id,
    )
    runner = GraphSkillModelRunner(
        session,
        provider=provider,
        tool_gateway=GraphPhaseToolGateway(
            session,
            operator=operator,
            tools=tools.handlers(),
        ),
        operator=operator,
    )

    result = await GraphGovernanceExecutionExecutor(
        runner=runner,
        tools=tools,
    ).run(
        GraphSkillInvocation(
            task_id=task.id,
            run_id=run.id,
            graph_run_id=graph.id,
            graph_node=graph.current_node,
            graph_cursor=graph.cursor,
            action_id=action_id,
            evidence_manifest_id=manifest_record.id,
            skill_name="execute-approved-governance-plan",
            skill_version="1.0.0",
            input_payload={
                "task_id": str(task.id),
                "run_id": str(run.id),
                "phase": "execute_and_verify",
                "evidence_refs": ["execution-outcomes:v1"],
                "plan_id": str(plan_id),
                "operation_ids": [str(failed_id), str(independent_id)],
            },
        )
    )

    assert {item.status for item in result.outcomes} == {"failed", "succeeded"}
    assert set(tools.outcomes) == {failed_id, independent_id}
