"""Server-bound phase tool gateway for ``new-agent-v1`` sub-agents."""

from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.mcp.agent_authorization import (
    AgentCapability,
    AgentToolAuthorizationError,
    AgentToolContext,
    require_agent_capability,
    require_agent_connector,
    require_agent_resource,
)
from app.core.security import OperatorContext
from app.models.agent_runtime import AgentRunRecord
from app.models.reconciliation import ReconciliationTask

AgentPhaseTool = Callable[[AgentToolContext, Mapping[str, object]], Awaitable[dict[str, Any]]]

_FORBIDDEN_ARGUMENT_KEYS = frozenset(
    {
        "credential",
        "credentials",
        "dsn",
        "filesystem_path",
        "path",
        "shell",
        "sql",
        "url",
    }
)


class AgentPhaseToolGateway:
    """Authorize a pre-registered phase tool against durable server-owned state."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        operator: OperatorContext,
        tools: Mapping[AgentCapability, AgentPhaseTool],
    ) -> None:
        self._session = session
        self._operator = operator
        self._tools = dict(tools)

    async def call(
        self,
        capability: AgentCapability,
        *,
        context: AgentToolContext,
        arguments: Mapping[str, object],
        resource_id: UUID | None = None,
        connector_id: str | None = None,
    ) -> dict[str, Any]:
        await self._authorize_durable_context(context)
        require_agent_capability(context, capability)
        if resource_id is not None:
            require_agent_resource(context, resource_id)
        if connector_id is not None:
            require_agent_connector(context, connector_id)
        if _contains_forbidden_argument(arguments):
            raise AgentToolAuthorizationError("arbitrary connector/tool arguments are forbidden")
        handler = self._tools.get(capability)
        if handler is None:
            raise AgentToolAuthorizationError("capability has no registered server tool")
        return await handler(context, arguments)

    async def _authorize_durable_context(self, context: AgentToolContext) -> None:
        if (
            context.operator_id != self._operator.operator_id
            or context.tenant_id != self._operator.tenant_id
        ):
            raise AgentToolAuthorizationError("operator context is not authorized")
        row = (
            await self._session.execute(
                select(AgentRunRecord, ReconciliationTask)
                .join(ReconciliationTask, ReconciliationTask.id == AgentRunRecord.task_id)
                .where(AgentRunRecord.id == context.run_id)
            )
        ).one_or_none()
        if row is None:
            raise AgentToolAuthorizationError("Agent run is not authorized")
        run, task = row
        if (
            run.task_id != context.task_id
            or run.tenant_id != context.tenant_id
            or task.tenant_id != context.tenant_id
            or run.phase != context.phase
            or run.conversation_id != context.conversation_id
            or task.workflow_version != "new-agent-v1"
        ):
            raise AgentToolAuthorizationError("Agent tool context does not match durable state")


def _contains_forbidden_argument(value: object, *, field: str | None = None) -> bool:
    if field is not None and field.casefold() in _FORBIDDEN_ARGUMENT_KEYS:
        return True
    if isinstance(value, Mapping):
        return any(
            _contains_forbidden_argument(item, field=str(key))
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_forbidden_argument(item) for item in value)
    return False
