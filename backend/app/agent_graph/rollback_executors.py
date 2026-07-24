"""Evidence-bounded rollback sub-agents for the controlled graph."""

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from app.agent_graph.governance_executors import GraphExecutionTools
from app.agent_graph.tools import GraphToolContext, GraphToolHandler
from app.ai.graph_subagents import GraphSkillInvocation, GraphSkillModelRunner
from app.ai.skills.contracts import AgentRollbackAssessment, AgentRollbackOutcome


class GraphRollbackEvidenceTools:
    def __init__(
        self,
        *,
        task_id: UUID,
        run_id: UUID,
        tenant_id: str,
        resource_id: str,
        verified_mutations: tuple[dict[str, Any], ...],
        conflict_operation_ids: tuple[UUID, ...] = (),
    ) -> None:
        self._task_id = task_id
        self._run_id = run_id
        self._tenant_id = tenant_id
        self._resource_id = resource_id
        self._verified_mutations = verified_mutations
        self._conflict_operation_ids = conflict_operation_ids

    def handlers(self) -> dict[str, GraphToolHandler]:
        return {
            "read_verified_mutations": self.read_verified_mutations,
            "read_restore_conflicts": self.read_restore_conflicts,
            "submit_restore_assessment": self.submit_restore_assessment,
        }

    async def read_verified_mutations(
        self,
        context: GraphToolContext,
        arguments: Mapping[str, object],
    ) -> dict[str, Any]:
        self._require_context(context)
        self._require_resource(arguments)
        return {
            "resource_id": self._resource_id,
            "verified_mutations": _redact_phone_values(
                list(self._verified_mutations)
            ),
        }

    async def read_restore_conflicts(
        self,
        context: GraphToolContext,
        arguments: Mapping[str, object],
    ) -> dict[str, Any]:
        self._require_context(context)
        self._require_resource(arguments)
        return {
            "resource_id": self._resource_id,
            "conflict_operation_ids": [
                str(item) for item in self._conflict_operation_ids
            ],
        }

    async def submit_restore_assessment(
        self,
        context: GraphToolContext,
        arguments: Mapping[str, object],
    ) -> dict[str, Any]:
        self._require_context(context)
        result = AgentRollbackAssessment.model_validate(arguments.get("submission"))
        return {
            "accepted": True,
            "restorable_count": len(result.restorable_operation_ids),
            "conflict_count": len(result.conflict_operation_ids),
        }

    def _require_context(self, context: GraphToolContext) -> None:
        if (
            context.task_id != self._task_id
            or context.run_id != self._run_id
            or context.tenant_id != self._tenant_id
        ):
            raise PermissionError("rollback evidence is outside current graph task")

    def _require_resource(self, arguments: Mapping[str, object]) -> None:
        if arguments.get("resource_id") != self._resource_id:
            raise ValueError("rollback fact resource is outside the manifest")


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


class GraphRollbackAssessmentExecutor:
    def __init__(self, *, runner: GraphSkillModelRunner) -> None:
        self._runner = runner

    async def run(
        self,
        invocation: GraphSkillInvocation,
        *,
        operation_ids: tuple[UUID, ...],
    ) -> AgentRollbackAssessment:
        expected = set(operation_ids)

        def validate(output: BaseModel) -> BaseModel:
            if not isinstance(output, AgentRollbackAssessment):
                raise ValueError("rollback assessment Skill returned another schema")
            restorable = set(output.restorable_operation_ids)
            conflicts = set(output.conflict_operation_ids)
            if restorable.intersection(conflicts) or restorable | conflicts != expected:
                raise ValueError(
                    "rollback assessment must exactly partition frozen operations"
                )
            return output

        result = await self._runner.run(
            invocation.model_copy(
                update={
                    "skill_name": "assess-agent-rollback-impact",
                    "skill_version": "1.0.0",
                }
            ),
            result_validator=validate,
        )
        if not isinstance(result.output, AgentRollbackAssessment):
            raise RuntimeError("validated rollback assessment changed type")
        return result.output


class GraphRollbackExecutionExecutor:
    def __init__(
        self,
        *,
        runner: GraphSkillModelRunner,
        tools: GraphExecutionTools,
    ) -> None:
        self._runner = runner
        self._tools = tools

    async def run(
        self,
        invocation: GraphSkillInvocation,
    ) -> AgentRollbackOutcome:
        expected = set(self._tools.operation_ids)

        def validate(output: BaseModel) -> BaseModel:
            if not isinstance(output, AgentRollbackOutcome):
                raise ValueError("rollback execution Skill returned another schema")
            actual_ids = tuple(item.operation_id for item in output.outcomes)
            if len(set(actual_ids)) != len(actual_ids) or set(actual_ids) != expected:
                raise ValueError(
                    "rollback execution must exactly cover frozen operations"
                )
            actual = self._tools.outcomes
            if set(actual) != expected:
                raise ValueError("rollback model omitted server execution tool calls")
            for outcome in output.outcomes:
                if outcome != actual[outcome.operation_id]:
                    raise ValueError("rollback model changed a server execution fact")
            return output

        result = await self._runner.run(
            invocation.model_copy(
                update={
                    "skill_name": "execute-approved-rollback",
                    "skill_version": "1.0.0",
                }
            ),
            result_validator=validate,
        )
        if not isinstance(result.output, AgentRollbackOutcome):
            raise RuntimeError("validated rollback execution changed type")
        return result.output
