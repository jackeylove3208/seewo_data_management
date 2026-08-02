"""Fact-bound model narration for terminal Agent graph reports."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_graph.tools import GraphToolContext, GraphToolHandler
from app.agent_reporting.service import AgentReportingService
from app.ai.graph_subagents import GraphSkillInvocation, GraphSkillModelRunner
from app.ai.skills.contracts import AgentGovernanceReport
from app.models.reporting import AgentReportRecord


@dataclass(frozen=True)
class GraphReportResult:
    report: AgentReportRecord
    invocation_id: UUID


class GraphReportFactTools:
    def __init__(
        self,
        *,
        task_id: UUID,
        run_id: UUID,
        tenant_id: str,
        resource_id: str,
        facts: Mapping[str, Any],
    ) -> None:
        self._task_id = task_id
        self._run_id = run_id
        self._tenant_id = tenant_id
        self._resource_id = resource_id
        self._facts = dict(facts)

    def handlers(self) -> dict[str, GraphToolHandler]:
        return {
            "read_report_fact_manifest": self.read_report_fact_manifest,
            "submit_report_narrative": self.submit_report_narrative,
        }

    async def read_report_fact_manifest(
        self,
        context: GraphToolContext,
        arguments: Mapping[str, object],
    ) -> dict[str, Any]:
        self._require_context(context)
        if arguments.get("resource_id") != self._resource_id:
            raise ValueError("report fact resource is outside the bound manifest")
        return {
            "resource_id": self._resource_id,
            "facts": _redact_phone_values(self._facts),
        }

    async def submit_report_narrative(
        self,
        context: GraphToolContext,
        arguments: Mapping[str, object],
    ) -> dict[str, Any]:
        self._require_context(context)
        report = AgentGovernanceReport.model_validate(arguments.get("submission"))
        return {
            "accepted": True,
            "schema_version": report.schema_version,
        }

    def _require_context(self, context: GraphToolContext) -> None:
        if (
            context.task_id != self._task_id
            or context.run_id != self._run_id
            or context.tenant_id != self._tenant_id
        ):
            raise PermissionError("report tool context is outside current graph task")


class GraphReportExecutor:
    def __init__(
        self,
        session: AsyncSession,
        *,
        runner: GraphSkillModelRunner,
    ) -> None:
        self._session = session
        self._runner = runner

    async def generate(
        self,
        invocation: GraphSkillInvocation,
        *,
        tenant_id: str,
        kind: str,
        terminal_state: str,
        facts: Mapping[str, Any],
        expected_rollback_eligible: bool,
    ) -> GraphReportResult:
        if invocation.graph_node not in {
            "generate_terminal_report",
            "termination_report",
            "abnormal_input_report",
            "generate_rollback_report",
        }:
            raise ValueError("report Skill is not allowed at the current graph node")
        expected_refs = tuple(invocation.input_payload.get("fact_refs", ()))
        input_diagnostics = facts.get("input_diagnostics")
        exclusive_reason_counts = (
            input_diagnostics.get("reason_counts")
            if isinstance(input_diagnostics, Mapping)
            else None
        )
        if isinstance(exclusive_reason_counts, Mapping):
            expected_exception_codes = {
                str(reason)
                for reason, value in exclusive_reason_counts.items()
                if isinstance(value, int) and value > 0
            }
        else:
            expected_exception_codes = {
                str(item["reason"])
                for item in facts.get("excluded_findings", ())
                if isinstance(item, Mapping) and item.get("reason")
            }

        def validate(output: BaseModel) -> BaseModel:
            if not isinstance(output, AgentGovernanceReport):
                raise ValueError("report Skill returned another schema")
            if output.fact_refs != expected_refs:
                raise ValueError("report narrative changed frozen fact references")
            if output.rollback_evidence_eligible is not expected_rollback_eligible:
                raise ValueError("report narrative changed rollback eligibility")
            actual_exception_codes = [
                item.reason_code for item in output.input_exception_analyses
            ]
            if len(actual_exception_codes) != len(set(actual_exception_codes)):
                raise ValueError("report narrative duplicated an input exception analysis")
            if set(actual_exception_codes) != expected_exception_codes:
                raise ValueError(
                    "report narrative did not cover the frozen input exception reasons"
                )
            return output

        result = await self._runner.run(
            invocation.model_copy(
                update={
                    "skill_name": "generate-agent-governance-report",
                    "skill_version": "1.0.0",
                }
            ),
            result_validator=validate,
        )
        output = result.output
        if not isinstance(output, AgentGovernanceReport):
            raise RuntimeError("validated report output changed type")
        included_quality_warnings = _included_quality_warning_analyses(facts)
        report = await AgentReportingService(self._session).generate(
            task_id=invocation.task_id,
            tenant_id=tenant_id,
            kind=kind,
            terminal_state=terminal_state,
            facts=facts,
            narrative={
                "title_zh": output.title_zh,
                "summary_zh": output.summary_zh,
                "input_exception_analyses": [
                    included_quality_warnings.get(
                        item.reason_code, item.model_dump(mode="json")
                    )
                    for item in output.input_exception_analyses
                ],
                "fact_refs": list(output.fact_refs),
            },
            generated_by="agent-graph-report-skill-v1",
        )
        return GraphReportResult(report=report, invocation_id=result.invocation_id)


def _included_quality_warning_analyses(
    facts: Mapping[str, Any],
) -> dict[str, dict[str, str]]:
    field_unavailable_findings = [
        item
        for item in facts.get("excluded_findings", ())
        if isinstance(item, Mapping)
        and item.get("reason") == "authority_field_unavailable"
    ]
    included_findings = [
        item
        for item in field_unavailable_findings
        if item.get("inclusion_state") == "included"
    ]
    if not included_findings:
        return {}

    entity_kinds = {
        str(evidence["entity_kind"])
        for item in included_findings
        if isinstance(evidence := item.get("safe_evidence"), Mapping)
        and evidence.get("entity_kind")
    }
    affected_fields = {
        str(field)
        for item in included_findings
        for field in item.get("affected_fields", ())
    }
    entity_zh = _localized_labels(entity_kinds, {"student": "学生"}, "记录")
    field_zh = _localized_labels(
        affected_fields,
        {"class_name": "班级信息", "email": "邮箱"},
        "字段信息",
    )
    count_zh = _reason_count(
        facts,
        "authority_field_unavailable",
        fallback=len(included_findings),
    )
    has_non_included_findings = len(included_findings) != len(
        field_unavailable_findings
    )
    impact_zh = (
        f"{field_zh}不可用仅作为数据质量提醒；允许同步的{entity_zh}"
        "仍保留在匹配与同步范围内，允许同步，其他记录按其排除或异常状态处理。"
        if has_non_included_findings
        else (
            f"{field_zh}不可用仅作为数据质量提醒；这些{entity_zh}"
            "仍保留在匹配与同步范围内，允许同步。"
        )
    )
    return {
        "authority_field_unavailable": {
            "reason_code": "authority_field_unavailable",
            "title_zh": f"权威{entity_zh}数据缺少{field_zh}",
            "analysis_zh": (
                f"权威{entity_zh}数据中有 {count_zh} 条记录缺少{field_zh}。"
            ),
            "impact_zh": impact_zh,
            "suggestion_zh": (
                f"建议补充{field_zh}以提升数据质量；已完成的同步无需重试。"
            ),
        }
    }


def _localized_labels(
    values: set[str],
    labels: Mapping[str, str],
    fallback: str,
) -> str:
    return "、".join(sorted(labels.get(value, value) for value in values)) or fallback


def _reason_count(
    facts: Mapping[str, Any],
    reason: str,
    *,
    fallback: int,
) -> int:
    input_diagnostics = facts.get("input_diagnostics")
    if not isinstance(input_diagnostics, Mapping):
        return fallback
    reason_counts = input_diagnostics.get("reason_counts")
    if not isinstance(reason_counts, Mapping):
        return fallback
    count = reason_counts.get(reason)
    return count if isinstance(count, int) and count >= 0 else fallback


def _redact_phone_values(value: object, *, field: str | None = None) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _redact_phone_values(item, field=str(key))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_phone_values(item, field=field) for item in value]
    if field is not None and "phone" in field.casefold() and value is not None:
        return "***"
    return value
