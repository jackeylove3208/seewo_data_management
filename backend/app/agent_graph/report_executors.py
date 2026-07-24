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

        def validate(output: BaseModel) -> BaseModel:
            if not isinstance(output, AgentGovernanceReport):
                raise ValueError("report Skill returned another schema")
            if output.fact_refs != expected_refs:
                raise ValueError("report narrative changed frozen fact references")
            if output.rollback_evidence_eligible is not expected_rollback_eligible:
                raise ValueError("report narrative changed rollback eligibility")
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
        report = await AgentReportingService(self._session).generate(
            task_id=invocation.task_id,
            tenant_id=tenant_id,
            kind=kind,
            terminal_state=terminal_state,
            facts=facts,
            narrative={
                "title_zh": output.title_zh,
                "summary_zh": output.summary_zh,
                "fact_refs": list(output.fact_refs),
            },
            generated_by="agent-graph-report-skill-v1",
        )
        return GraphReportResult(report=report, invocation_id=result.invocation_id)


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
