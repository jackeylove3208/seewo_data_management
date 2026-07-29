from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.mcp.authorization import (
    ToolAuthorizationError,
    ToolContext,
    require_difference,
)
from app.ai.mcp.tools.candidate_search import search_candidates
from app.ai.mcp.tools.difference_context import read_difference_context
from app.ai.mcp.tools.execution_context import read_execution_context
from app.ai.mcp.tools.mapping_rules import read_mapping_rules
from app.ai.mcp.tools.rematch_evidence import read_candidate_evidence
from app.differences.field_policies import FieldComparisonPolicy
from app.repositories.differences import DifferenceRepository

READ_ONLY_TOOL_NAMES = frozenset(
    {
        "difference_context",
        "candidate_search",
        "rematch_candidate_evidence",
        "mapping_rules",
        "execution_context",
    }
)


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    payload: dict[str, Any]
    trace_id: str = Field(min_length=1, max_length=128)


class MCPToolGateway:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.differences = DifferenceRepository(session)
        self.policy = FieldComparisonPolicy()

    @property
    def tool_names(self) -> set[str]:
        return set(READ_ONLY_TOOL_NAMES)

    async def call(
        self,
        name: str,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        if name not in READ_ONLY_TOOL_NAMES:
            raise ToolAuthorizationError("tool is not allowed")
        difference_id = _difference_id(arguments)
        difference = await require_difference(context, difference_id, self.differences)
        if name == "difference_context":
            payload = read_difference_context(difference)
        elif name == "candidate_search":
            payload = await search_candidates(
                self.differences.session,
                difference,
                str(arguments.get("query", "")),
                _integer_argument(arguments, "top_k", default=5),
            )
        elif name == "rematch_candidate_evidence":
            work_item_id = _uuid_argument(arguments, "work_item_id")
            evidence_payload = await read_candidate_evidence(
                self.differences.session,
                task_id=context.task_id,
                tenant_id=context.tenant_id,
                work_item_id=work_item_id,
            )
            if evidence_payload is None:
                raise ToolAuthorizationError("rematch item not authorized")
            payload = evidence_payload
        elif name == "mapping_rules":
            payload = read_mapping_rules(difference, self.policy)
        else:
            payload = read_execution_context(difference)
        return ToolResult(payload=payload, trace_id=str(uuid4()))

    async def close_read_transaction(self) -> None:
        if self.session.in_transaction():
            await self.session.rollback()


def _difference_id(arguments: dict[str, Any]) -> UUID:
    value = arguments.get("difference_id")
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as error:
        raise ValueError("difference_id must be a UUID") from error


def _integer_argument(arguments: dict[str, Any], name: str, *, default: int) -> int:
    value = arguments.get(name, default)
    if not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    return value


def _uuid_argument(arguments: dict[str, Any], name: str) -> UUID:
    try:
        return UUID(str(arguments.get(name)))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a UUID") from error
