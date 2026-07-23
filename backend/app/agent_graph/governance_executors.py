"""Server-owned human gates for controlled graph governance."""

import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_graph.repository import AgentGraphRepository
from app.agent_graph.tools import GraphToolContext, GraphToolHandler
from app.ai.graph_subagents import GraphSkillInvocation, GraphSkillModelRunner
from app.ai.skills.contracts import GovernanceExecutionOutcome, OperationOutcome
from app.models.agent_graph import AgentHumanGateRecord


@dataclass(frozen=True)
class FrozenApprovalDraft:
    group_key: str
    finding_ids: tuple[UUID, ...]
    issue_kind: str
    entity_kind: str
    operation: str
    risk: str
    policy_version: str


class GraphHumanGateService:
    def __init__(self, session: AsyncSession) -> None:
        self._repository = AgentGraphRepository(session)

    async def freeze_high_risk_approvals(
        self,
        *,
        graph_run_id: UUID,
        cursor: int,
        groups: tuple[FrozenApprovalDraft, ...],
    ) -> tuple[AgentHumanGateRecord, ...]:
        records: list[AgentHumanGateRecord] = []
        seen: set[UUID] = set()
        for group in groups:
            if group.risk != "high":
                raise ValueError("only server-classified high risk may create approval gates")
            if not group.finding_ids or len(set(group.finding_ids)) != len(
                group.finding_ids
            ):
                raise ValueError("approval group members must be non-empty and unique")
            if seen.intersection(group.finding_ids):
                raise ValueError("a finding cannot appear in multiple approval gates")
            seen.update(group.finding_ids)
            payload = {
                "group_key": group.group_key,
                "finding_ids": [str(item) for item in group.finding_ids],
                "issue_kind": group.issue_kind,
                "entity_kind": group.entity_kind,
                "operation": group.operation,
                "risk": group.risk,
                "policy_version": group.policy_version,
            }
            records.append(
                await self._repository.record_human_gate(
                    graph_run_id=graph_run_id,
                    cursor=cursor,
                    gate_kind="high_risk_approval",
                    member_ids=tuple(str(item) for item in group.finding_ids),
                    content_hash=_hash(payload),
                    status="pending",
                )
            )
        return tuple(records)


ExecuteOperation = Callable[[UUID], Awaitable[OperationOutcome]]


class GraphExecutionTools:
    """Expose only server-frozen operation IDs and idempotent execution callbacks."""

    def __init__(
        self,
        *,
        task_id: UUID,
        run_id: UUID,
        tenant_id: str,
        plan_id: UUID,
        operation_ids: tuple[UUID, ...],
        execute_operation: ExecuteOperation,
    ) -> None:
        if not operation_ids or len(set(operation_ids)) != len(operation_ids):
            raise ValueError("execution tools require unique frozen operation IDs")
        self._task_id = task_id
        self._run_id = run_id
        self._tenant_id = tenant_id
        self._plan_id = plan_id
        self._operation_ids = operation_ids
        self._execute_operation = execute_operation
        self._outcomes: dict[UUID, OperationOutcome] = {}

    def handlers(self) -> dict[str, GraphToolHandler]:
        return {
            "read_execution_plan": self.read_execution_plan,
            "read_ready_operations": self.read_ready_operations,
            "request_operation_execution": self.request_operation_execution,
            "read_operation_verification": self.read_operation_verification,
        }

    @property
    def outcomes(self) -> Mapping[UUID, OperationOutcome]:
        return dict(self._outcomes)

    @property
    def operation_ids(self) -> tuple[UUID, ...]:
        return self._operation_ids

    async def read_execution_plan(
        self,
        context: GraphToolContext,
        arguments: Mapping[str, object],
    ) -> dict[str, Any]:
        self._require_context(context)
        self._require_plan_resource(arguments)
        return {
            "plan_id": str(self._plan_id),
            "operation_ids": [str(item) for item in self._operation_ids],
            "target_role": "target",
            "server_ordered": True,
        }

    async def read_ready_operations(
        self,
        context: GraphToolContext,
        arguments: Mapping[str, object],
    ) -> dict[str, Any]:
        self._require_context(context)
        self._require_plan_resource(arguments)
        return {
            "ready_operation_ids": [
                str(item) for item in self._operation_ids if item not in self._outcomes
            ]
        }

    async def request_operation_execution(
        self,
        context: GraphToolContext,
        arguments: Mapping[str, object],
    ) -> dict[str, Any]:
        self._require_context(context)
        operation_id = _operation_resource(arguments)
        if operation_id not in self._operation_ids:
            raise ValueError("operation is outside frozen execution plan")
        outcome = self._outcomes.get(operation_id)
        if outcome is None:
            outcome = await self._execute_operation(operation_id)
            if outcome.operation_id != operation_id:
                raise ValueError("execution callback returned another operation")
            self._outcomes[operation_id] = outcome
        return outcome.model_dump(mode="json")

    async def read_operation_verification(
        self,
        context: GraphToolContext,
        arguments: Mapping[str, object],
    ) -> dict[str, Any]:
        self._require_context(context)
        operation_id = _operation_resource(arguments)
        outcome = self._outcomes.get(operation_id)
        if outcome is None:
            raise ValueError("operation has no persisted execution outcome")
        return outcome.model_dump(mode="json")

    def _require_context(self, context: GraphToolContext) -> None:
        if (
            context.task_id != self._task_id
            or context.run_id != self._run_id
            or context.tenant_id != self._tenant_id
        ):
            raise PermissionError("execution tool context is outside graph task")

    def _require_plan_resource(self, arguments: Mapping[str, object]) -> None:
        if arguments.get("resource_id") != f"execution-plan:{self._plan_id}":
            raise ValueError("execution plan resource is not authorized")


class GraphGovernanceExecutionExecutor:
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
    ) -> GovernanceExecutionOutcome:
        expected_ids = set(self._tools.operation_ids)

        def validate(output: BaseModel) -> BaseModel:
            if not isinstance(output, GovernanceExecutionOutcome):
                raise ValueError("execution Skill returned another schema")
            outcome_ids = tuple(item.operation_id for item in output.outcomes)
            if len(set(outcome_ids)) != len(outcome_ids) or set(outcome_ids) != expected_ids:
                raise ValueError("execution outcome must exactly cover frozen operations")
            actual = self._tools.outcomes
            if set(actual) != expected_ids:
                raise ValueError("model claimed outcomes for operations not executed by tools")
            for item in output.outcomes:
                persisted = actual[item.operation_id]
                if item != persisted:
                    raise ValueError("model execution outcome differs from server fact")
            return output

        result = await self._runner.run(
            invocation.model_copy(
                update={
                    "skill_name": "execute-approved-governance-plan",
                    "skill_version": "1.0.0",
                }
            ),
            result_validator=validate,
        )
        if not isinstance(result.output, GovernanceExecutionOutcome):
            raise RuntimeError("validated execution output changed type")
        return result.output


def _hash(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _operation_resource(arguments: Mapping[str, object]) -> UUID:
    value = arguments.get("resource_id")
    if not isinstance(value, str) or not value.startswith("operation:"):
        raise ValueError("operation resource is invalid")
    try:
        return UUID(value.removeprefix("operation:"))
    except ValueError as error:
        raise ValueError("operation resource is invalid") from error
