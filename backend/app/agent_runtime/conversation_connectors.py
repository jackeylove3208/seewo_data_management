from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.api_connectors.registry import ProviderRegistry
from app.api_connectors.repository import ApiConnectionRepository
from app.schemas.agent_api import AgentApiConnectionCard
from app.schemas.agent_conversation import (
    ConversationApiConnection,
    ConversationApiProvider,
)


@dataclass(frozen=True, slots=True)
class ConversationApiCatalog:
    providers: tuple[ConversationApiProvider, ...]
    connections: tuple[ConversationApiConnection, ...]

    def card(
        self,
        *,
        provider_id: str | None = None,
        connection_id: UUID | None = None,
    ) -> AgentApiConnectionCard | None:
        connection = next(
            (
                item
                for item in self.connections
                if item.connection_id == connection_id
            ),
            None,
        )
        selected_provider_id = (
            connection.provider_id if connection is not None else provider_id
        )
        provider = next(
            (
                item
                for item in self.providers
                if item.provider_id == selected_provider_id
            ),
            None,
        )
        if provider is None:
            return None
        if connection is None:
            connection = next(
                (
                    item
                    for item in self.connections
                    if item.provider_id == provider.provider_id
                ),
                None,
            )
        if connection is None:
            return AgentApiConnectionCard(
                provider_id=provider.provider_id,
                state="configuration_required",
                required_secret_fields=provider.required_secret_fields,
                display_name=_temporary_display_name(provider.provider_id),
            )
        return AgentApiConnectionCard(
            provider_id=provider.provider_id,
            state=connection.state,
            required_secret_fields=provider.required_secret_fields,
            connection_id=connection.connection_id,
            display_name=connection.display_name,
            capabilities=connection.capabilities,
            visibility_summary=connection.visibility_summary,
            safe_error_code=connection.last_safe_error_code,
        )


async def load_conversation_api_catalog(
    session: AsyncSession,
    *,
    tenant_id: str,
    conversation_id: UUID,
    registry: ProviderRegistry,
) -> ConversationApiCatalog:
    providers = tuple(
        ConversationApiProvider(
            provider_id=manifest.provider_id,
            supported_entities=tuple(sorted(manifest.supported_entities)),
            required_secret_fields=manifest.required_secret_fields,
        )
        for manifest in (
            registry.manifest(provider_id)
            for provider_id in registry.provider_ids()
        )
    )
    records = await ApiConnectionRepository(session).list_ephemeral_for_conversation(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
    )
    connections = tuple(
        ConversationApiConnection(
            connection_id=record.id,
            provider_id=record.provider_id,
            display_name=record.display_name,
            state=record.state,
            capabilities={
                str(key): value
                for key, value in record.capabilities.items()
                if isinstance(value, bool)
            },
            visibility_summary={
                str(key): value
                for key, value in record.visibility_summary.items()
                if isinstance(value, (str, int, bool)) or value is None
            },
            last_safe_error_code=record.last_safe_error_code,
        )
        for record in records
    )
    return ConversationApiCatalog(providers=providers, connections=connections)


def _temporary_display_name(provider_id: str) -> str:
    provider_name = {
        "dingtalk": "钉钉",
        "wecom": "企业微信",
    }.get(provider_id, provider_id)
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"{provider_name}临时连接-{timestamp}"
