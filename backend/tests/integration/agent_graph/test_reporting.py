from uuid import uuid4

import pytest
from sqlalchemy import select

from app.agent_graph.evidence import build_evidence_manifest
from app.agent_graph.report_executors import (
    GraphReportExecutor,
    GraphReportFactTools,
    _included_quality_warning_analyses,
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
        ],
        "input_diagnostics": {
            "marked_input_counts": {"authoritative": 7, "target": 0},
            "unique_marked_input_count": 7,
            "reason_counts": {"authority_field_unavailable": 7},
            "overlapped_reason_counts": {},
            "unavailable_field_counts": {"class_name": 7},
            "identity_absent_count": 0,
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
                    "title_zh": "模型错误标题：缺字段学生已排除",
                    "summary_zh": "请补充班级信息后重新运行，现有任务未完成。",
                    "input_exception_analyses": [
                        {
                            "reason_code": "authority_field_unavailable",
                            "title_zh": "模型错误地声称学生被排除",
                            "analysis_zh": "模型错误地声称学生无法参与匹配。",
                            "impact_zh": (
                                "这些学生记录无法可靠匹配，已从治理范围排除。"
                            ),
                            "suggestion_zh": (
                                "请补充班级信息后强制重新运行任务。"
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
    narrative = result.report.content["narrative"]
    assert narrative["title_zh"] == "数据同步任务报告"
    assert narrative["summary_zh"] == (
        "来源字段缺失已记录为质量提醒；允许同步的记录仍参与匹配与同步，"
        "具体执行结果见下方服务端事实。"
    )
    assert "缺字段学生已排除" not in str(narrative)
    assert "请补充班级信息后重新运行" not in str(narrative)
    analyses = result.report.content["narrative"]["input_exception_analyses"]
    assert analyses == [
        {
            "reason_code": "authority_field_unavailable",
            "title_zh": "权威学生数据缺少班级信息",
            "analysis_zh": "权威学生数据中有 7 条记录缺少班级信息。",
            "impact_zh": (
                "班级信息不可用仅作为数据质量提醒；这些学生"
                "仍保留在匹配与同步范围内，允许同步。"
            ),
            "suggestion_zh": "建议补充班级信息以提升数据质量；已完成的同步无需重试。",
        }
    ]
    assert "已从治理范围排除" not in str(analyses)
    assert "强制重新运行任务" not in str(analyses)
    assert result.report.rollback_eligible is True
    invocation = await session.scalar(
        select(AgentSubAgentInvocationRecord).where(
            AgentSubAgentInvocationRecord.skill_name
            == "generate-agent-governance-report"
        )
    )
    assert invocation is not None
    assert invocation.execution_mode == "skill_model"


def test_included_quality_warning_uses_reason_count_not_missing_field_count() -> None:
    analyses = _included_quality_warning_analyses(
        {
            "excluded_findings": [
                {
                    "reason": "authority_field_unavailable",
                    "affected_fields": ["class_name", "email"],
                    "inclusion_state": "included",
                    "safe_evidence": {
                        "entity_kind": "student",
                        "missing_count": 2,
                    },
                }
            ],
            "input_diagnostics": {
                "reason_counts": {"authority_field_unavailable": 1},
                "unavailable_field_counts": {"class_name": 1, "email": 1},
            },
        }
    )

    assert analyses["authority_field_unavailable"]["analysis_zh"] == (
        "权威学生数据中有 1 条记录缺少班级信息、邮箱。"
    )


def test_included_quality_warning_describes_mixed_inclusion_states_safely() -> None:
    analyses = _included_quality_warning_analyses(
        {
            "excluded_findings": [
                {
                    "reason": "authority_field_unavailable",
                    "affected_fields": ["class_name"],
                    "inclusion_state": "included",
                    "safe_evidence": {"entity_kind": "student"},
                },
                {
                    "reason": "authority_field_unavailable",
                    "affected_fields": ["class_name"],
                    "inclusion_state": "excluded",
                    "safe_evidence": {"entity_kind": "student"},
                },
            ],
            "input_diagnostics": {
                "reason_counts": {"authority_field_unavailable": 2},
                "unavailable_field_counts": {"class_name": 2},
            },
        }
    )

    impact = analyses["authority_field_unavailable"]["impact_zh"]
    assert impact == (
        "班级信息不可用仅作为数据质量提醒；允许同步的记录仍保留在匹配与同步范围内；"
        "其他记录按排除或异常状态处理。"
    )
    assert "这些学生均允许同步" not in impact


def test_included_quality_warning_ignores_pure_overlapped_reason() -> None:
    analyses = _included_quality_warning_analyses(
        {
            "excluded_findings": [
                {
                    "reason": "authority_field_unavailable",
                    "affected_fields": ["email"],
                    "inclusion_state": "included",
                    "safe_evidence": {"entity_kind": "student"},
                }
            ],
            "input_diagnostics": {
                "reason_counts": {"authority_identity_absent": 1},
                "overlapped_reason_counts": {"authority_field_unavailable": 1},
                "unavailable_field_counts": {},
            },
        }
    )

    assert analyses == {}


def test_included_quality_warning_uses_exclusive_count_and_fields() -> None:
    analyses = _included_quality_warning_analyses(
        {
            "excluded_findings": [
                *[
                    {
                        "reason": "authority_field_unavailable",
                        "affected_fields": ["class_name"],
                        "inclusion_state": "included",
                        "safe_evidence": {"entity_kind": "student"},
                    }
                    for _ in range(3)
                ],
                *[
                    {
                        "reason": "authority_field_unavailable",
                        "affected_fields": [field],
                        "inclusion_state": "included",
                        "safe_evidence": {"entity_kind": "teacher"},
                    }
                    for field in ("email", "number", "phone", "email")
                ],
            ],
            "input_diagnostics": {
                "reason_counts": {"authority_field_unavailable": 3},
                "overlapped_reason_counts": {"authority_field_unavailable": 4},
                "unavailable_field_counts": {"class_name": 3},
            },
        }
    )

    warning = analyses["authority_field_unavailable"]
    assert warning["analysis_zh"] == "权威学生数据中有 3 条记录缺少班级信息。"
    assert "邮箱" not in str(warning)
    assert "编号" not in str(warning)
    assert "手机号" not in str(warning)
    assert "教师" not in str(warning)


def test_included_quality_warning_uses_neutral_entity_for_same_field_overlap() -> None:
    analyses = _included_quality_warning_analyses(
        {
            "excluded_findings": [
                {
                    "reason": "authority_field_unavailable",
                    "affected_fields": ["email"],
                    "inclusion_state": "included",
                    "safe_evidence": {"entity_kind": "student"},
                },
                {
                    "reason": "authority_field_unavailable",
                    "affected_fields": ["email"],
                    "inclusion_state": "included",
                    "safe_evidence": {"entity_kind": "teacher"},
                },
            ],
            "input_diagnostics": {
                "reason_counts": {"authority_field_unavailable": 1},
                "overlapped_reason_counts": {"authority_field_unavailable": 1},
                "unavailable_field_counts": {"email": 1},
            },
        }
    )

    warning = analyses["authority_field_unavailable"]
    assert warning["analysis_zh"] == "权威记录数据中有 1 条记录缺少邮箱。"
    assert warning["impact_zh"] == (
        "邮箱不可用仅作为数据质量提醒；允许同步的记录仍保留在匹配与同步范围内；"
        "其他记录按其更高优先级异常状态处理。"
    )
    assert "教师" not in str(warning)
    assert "teacher" not in str(warning)
    assert "学生" not in str(warning)


def test_included_quality_warning_localizes_unambiguous_department_and_teacher() -> None:
    analyses = _included_quality_warning_analyses(
        {
            "excluded_findings": [
                {
                    "reason": "authority_field_unavailable",
                    "affected_fields": ["email"],
                    "inclusion_state": "included",
                    "safe_evidence": {"entity_kind": entity_kind},
                }
                for entity_kind in ("department", "teacher")
            ],
            "input_diagnostics": {
                "reason_counts": {"authority_field_unavailable": 2},
                "unavailable_field_counts": {"email": 2},
            },
        }
    )

    warning = str(analyses["authority_field_unavailable"])
    assert "教师" in warning
    assert "部门" in warning
    assert "teacher" not in warning
    assert "department" not in warning


def test_included_quality_warning_localizes_all_supported_fields() -> None:
    analyses = _included_quality_warning_analyses(
        {
            "excluded_findings": [
                {
                    "reason": "authority_field_unavailable",
                    "affected_fields": [
                        "category",
                        "name",
                        "number",
                        "class_name",
                        "phone",
                        "email",
                    ],
                    "inclusion_state": "included",
                    "safe_evidence": {"entity_kind": "student"},
                }
            ],
            "input_diagnostics": {
                "reason_counts": {"authority_field_unavailable": 1},
                "unavailable_field_counts": {
                    "category": 1,
                    "name": 1,
                    "number": 1,
                    "class_name": 1,
                    "phone": 1,
                    "email": 1,
                },
            },
        }
    )

    warning = str(analyses["authority_field_unavailable"])
    for label in ("类别", "名称", "编号", "班级信息", "手机号", "邮箱"):
        assert label in warning
    for raw_field in ("category", "name", "number", "class_name", "phone", "email"):
        assert raw_field not in warning
