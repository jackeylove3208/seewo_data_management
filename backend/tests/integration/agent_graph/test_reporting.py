from uuid import uuid4

import pytest
from sqlalchemy import select

from app.agent_graph.evidence import build_evidence_manifest
from app.agent_graph.report_executors import (
    GraphReportExecutor,
    GraphReportFactTools,
)
from app.agent_graph.repository import AgentGraphRepository
from app.agent_graph.tools import GraphPhaseToolGateway
from app.agent_runtime.repository import AgentRuntimeRepository
from app.agent_runtime.state_machine import AgentRunKind
from app.ai.graph_subagents import GraphSkillInvocation, GraphSkillModelRunner
from app.ai.providers.base import LLMRequest, LLMResponse, ModelUsage
from app.core.security import OperatorContext
from app.models.agent_graph import AgentSubAgentInvocationRecord
from app.models.reconciliation import ReconciliationTask


class ReportProvider:
    def __init__(self, outputs):
        self.outputs = list(outputs)

    async def complete_json_once(self, _request: LLMRequest) -> LLMResponse:
        return LLMResponse(
            output=self.outputs.pop(0),
            provider="scripted",
            model="report-model",
            request_id=str(uuid4()),
            usage=ModelUsage(input_tokens=10, output_tokens=10),
        )


@pytest.mark.asyncio
async def test_graph_report_uses_model_narrative_but_server_facts(session) -> None:
    task = ReconciliationTask(
        tenant_id="school-graph-report",
        scope_id="all",
        snapshot_mode="full",
        entity_types=["student"],
        status="running",
        stage="reporting",
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
        initial_node="generate_terminal_report",
    )
    resource_id = "report-facts:terminal"
    fact_ref = "fact:mutation-summary"
    manifest = build_evidence_manifest(
        tenant_ref=f"tenant-ref:{graph.id}",
        task_id=str(task.id),
        run_id=str(run.id),
        graph_node=graph.current_node,
        action_id="generate_terminal_report",
        resource_ids=(resource_id,),
        allowed_evidence_refs=(fact_ref,),
    )
    manifest_record = await AgentGraphRepository(session).record_manifest(
        graph_run_id=graph.id,
        cursor=graph.cursor,
        graph_node=graph.current_node,
        action_id=manifest.action_id,
        manifest=manifest.model_dump(mode="json"),
        content_hash=manifest.content_hash,
        record_id=manifest.manifest_id,
    )
    facts = {
        "excluded_findings": [
            {
                "source_role": "authoritative",
                "reason": "authority_field_unavailable",
                "affected_fields": ["class_name"],
                "inclusion_state": "included",
                "disposition": "source_field_unavailable",
                "safe_evidence": {
                    "entity_kind": "student",
                    "missing_count": 7,
                    "missing_fields": "class_name",
                },
            },
            {
                "source_role": "authoritative",
                "reason": "authority_identity_absent",
                "affected_fields": ["number"],
                "inclusion_state": "anomaly",
                "disposition": "mandatory_ai_anomaly",
                "safe_evidence": {
                    "entity_kind": "department",
                    "missing_count": 4,
                    "missing_fields": "number",
                },
            },
        ],
        "input_diagnostics": {
            "marked_input_counts": {"authoritative": 4, "target": 0},
            "unique_marked_input_count": 4,
            "reason_counts": {"authority_identity_absent": 4},
            "overlapped_reason_counts": {"authority_field_unavailable": 4},
            "unavailable_field_counts": {},
            "identity_absent_count": 4,
        },
        "mutations": [
            {
                "id": str(uuid4()),
                "status": "succeeded",
                "verification": {"valid": True},
            }
        ],
        "student_phone": "13800138000",
    }
    operator = OperatorContext(
        operator_id="demo-operator",
        tenant_id=task.tenant_id,
    )
    tools = GraphReportFactTools(
        task_id=task.id,
        run_id=run.id,
        tenant_id=task.tenant_id,
        resource_id=resource_id,
        facts=facts,
    )
    provider = ReportProvider(
        [
            {
                "result": {
                    "tool_call": {
                        "name": "read_report_fact_manifest",
                        "arguments": {"resource_id": resource_id},
                    }
                }
            },
            {
                "result": {
                    "schema_version": "agent-contract-v1",
                    "title_zh": "学校数据同步完成报告",
                    "summary_zh": "本次同步已完成，治理操作已经服务端验证。",
                    "input_exception_analyses": [
                        {
                            "reason_code": "authority_identity_absent",
                            "title_zh": "权威部门数据缺少身份标识",
                            "analysis_zh": (
                                "钉钉权威数据中有 4 条部门记录缺少身份标识。"
                            ),
                            "impact_zh": (
                                "这些部门记录无法可靠匹配，已从治理范围排除。"
                            ),
                            "suggestion_zh": (
                                "请补充可用于匹配的部门编号后重新运行任务。"
                            ),
                        }
                    ],
                    "fact_refs": [fact_ref],
                    "rollback_evidence_eligible": True,
                }
            },
        ]
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

    result = await GraphReportExecutor(session, runner=runner).generate(
        GraphSkillInvocation(
            task_id=task.id,
            run_id=run.id,
            graph_run_id=graph.id,
            graph_node=graph.current_node,
            graph_cursor=graph.cursor,
            action_id="generate_terminal_report",
            evidence_manifest_id=manifest_record.id,
            skill_name="generate-agent-governance-report",
            skill_version="1.0.0",
            input_payload={
                "task_id": str(task.id),
                "run_id": str(run.id),
                "phase": "generate_report",
                "evidence_refs": [fact_ref],
                "outcome": "completed",
                "fact_refs": [fact_ref],
            },
        ),
        tenant_id=task.tenant_id,
        kind="sync",
        terminal_state="completed",
        facts=facts,
        expected_rollback_eligible=True,
    )

    assert result.report.facts["student_phone"] == "13800138000"
    assert "13800138000" not in str(result.report.content)
    assert result.report.generated_by == "agent-graph-report-skill-v1"
    analyses = result.report.content["narrative"]["input_exception_analyses"]
    assert analyses == [
        {
            "reason_code": "authority_identity_absent",
            "title_zh": "权威部门数据缺少身份标识",
            "analysis_zh": "钉钉权威数据中有 4 条部门记录缺少身份标识。",
            "impact_zh": "这些部门记录无法可靠匹配，已从治理范围排除。",
            "suggestion_zh": "请补充可用于匹配的部门编号后重新运行任务。",
        }
    ]
    assert result.report.rollback_eligible is True
    invocation = await session.scalar(
        select(AgentSubAgentInvocationRecord).where(
            AgentSubAgentInvocationRecord.skill_name
            == "generate-agent-governance-report"
        )
    )
    assert invocation is not None
    assert invocation.execution_mode == "skill_model"
