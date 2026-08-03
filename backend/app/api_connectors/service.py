from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_connectors.contracts import (
    ApiProviderError,
    ProviderManifest,
    SafeApiConnection,
)
from app.api_connectors.dingtalk_configuration import (
    ApiConnectionValidationError,
    redact_server_configuration,
    validate_new_task_configuration,
)
from app.api_connectors.policy import task_ephemeral_credentials_expired
from app.api_connectors.registry import ProviderRegistry
from app.api_connectors.repository import ApiConnectionRepository
from app.api_connectors.secrets import (
    EncryptedDatabaseSecretStore,
    revoke_conversation_ephemeral_connections,
)
from app.models.agent_runtime import AgentConversationRecord
from app.models.api_connectors import ApiConnectionRecord

__all__ = [
    "ApiConnectionConflictError",
    "ApiConnectionNotFoundError",
    "ApiConnectionService",
    "ApiConnectionValidationError",
]


class ApiConnectionNotFoundError(LookupError):
    pass


class ApiConnectionConflictError(RuntimeError):
    pass


class ApiConnectionService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        registry: ProviderRegistry,
        fernet_key: bytes | str | SecretStr,
    ) -> None:
        self._repository = ApiConnectionRepository(session)
        self._secrets = EncryptedDatabaseSecretStore(session, fernet_key=fernet_key)
        self._registry = registry

    def providers(self) -> tuple[ProviderManifest, ...]:
        return tuple(
            self._registry.manifest(provider_id)
            for provider_id in self._registry.provider_ids()
        )

    async def create(
        self,
        *,
        tenant_id: str,
        operator_id: str,
        provider_id: str,
        display_name: str,
        public_configuration: Mapping[str, object],
        secret: Mapping[str, str],
        scope: str = "persistent",
        conversation_id: UUID | None = None,
    ) -> SafeApiConnection:
        manifest = self._manifest(provider_id)
        _validate_secret_shape(secret, manifest)
        normalized_display_name = display_name.strip()
        if not normalized_display_name:
            raise ApiConnectionValidationError("connection display name must be non-blank")
        existing = await self._repository.get_by_display_name(
            tenant_id=tenant_id,
            display_name=normalized_display_name,
        )
        if existing is not None:
            raise ApiConnectionConflictError("connection display name already exists")
        if scope not in {"persistent", "task_ephemeral"}:
            raise ApiConnectionValidationError("connection scope is invalid")
        if (scope == "task_ephemeral") != (conversation_id is not None):
            raise ApiConnectionValidationError(
                "task-ephemeral connections require a conversation"
            )
        normalized_public_configuration = dict(public_configuration)
        if scope == "task_ephemeral":
            if provider_id == "dingtalk":
                normalized_public_configuration = validate_new_task_configuration(
                    public_configuration
                )
            assert conversation_id is not None
            owned_conversation_id = await self._repository.session.scalar(
                select(AgentConversationRecord.id)
                .where(
                    AgentConversationRecord.id == conversation_id,
                    AgentConversationRecord.tenant_id == tenant_id,
                    AgentConversationRecord.created_by == operator_id,
                    AgentConversationRecord.status == "active",
                )
                .with_for_update()
            )
            if owned_conversation_id is None:
                raise ApiConnectionValidationError(
                    "active conversation is unavailable"
                )
            await revoke_conversation_ephemeral_connections(
                self._repository.session,
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                reason="superseded",
            )

        secret_ref = await self._secrets.put(tenant_id=tenant_id, payload=secret)
        record = ApiConnectionRecord(
            tenant_id=tenant_id,
            provider_id=provider_id,
            display_name=normalized_display_name,
            scope=scope,
            conversation_id=conversation_id,
            public_configuration=normalized_public_configuration,
            secret_ref=secret_ref,
            manifest_version=manifest.manifest_version,
            adapter_version=manifest.adapter_version,
            capabilities={},
            visibility_summary={},
            state="pending",
            last_tested_at=None,
            last_safe_error_code=None,
            created_by=operator_id,
            updated_by=operator_id,
        )
        await self._repository.add(record)
        return _safe_view(record)

    async def list(self, *, tenant_id: str) -> tuple[SafeApiConnection, ...]:
        return tuple(
            _safe_view(record)
            for record in await self._repository.list_for_tenant(tenant_id)
        )

    async def get(
        self,
        *,
        tenant_id: str,
        connection_id: UUID,
    ) -> SafeApiConnection:
        return _safe_view(await self._owned(connection_id, tenant_id))

    async def test(
        self,
        *,
        tenant_id: str,
        operator_id: str,
        connection_id: UUID,
        conversation_id: UUID | None = None,
    ) -> SafeApiConnection:
        record = await self._owned_for_update(connection_id, tenant_id)
        if record.state == "disabled":
            raise ApiConnectionConflictError("connection is disabled")
        if record.scope == "task_ephemeral" and (
            record.created_by != operator_id
            or record.conversation_id != conversation_id
            or record.task_id is not None
            or record.credentials_revoked_at is not None
            or task_ephemeral_credentials_expired(record.created_at)
        ):
            raise ApiConnectionConflictError(
                "task-ephemeral connection cannot be tested"
            )
        manifest = self._manifest(record.provider_id)
        if (
            record.manifest_version != manifest.manifest_version
            or record.adapter_version != manifest.adapter_version
        ):
            raise ApiConnectionConflictError("connection provider contract is stale")
        secret = await self._secrets.get(
            tenant_id=tenant_id,
            secret_ref=record.secret_ref,
        )
        adapter = self._registry.adapter(record.provider_id)
        try:
            result = await adapter.test_connection(record.public_configuration, secret)
        except ApiProviderError as error:
            record.capabilities = {}
            record.visibility_summary = {}
            record.state = "invalid"
            record.last_safe_error_code = error.safe_code
        else:
            record.capabilities = dict(result.capabilities)
            record.visibility_summary = dict(result.visibility_summary)
            record.state = "active" if result.eligible else "invalid"
            record.last_safe_error_code = result.safe_error_code
        record.last_tested_at = datetime.now(UTC)
        record.updated_by = operator_id
        return _safe_view(record)

    async def rotate(
        self,
        *,
        tenant_id: str,
        operator_id: str,
        connection_id: UUID,
        secret: Mapping[str, str],
        public_configuration: Mapping[str, object] | None = None,
        conversation_id: UUID | None = None,
    ) -> SafeApiConnection:
        record = await self._owned_for_update(connection_id, tenant_id)
        if record.state == "disabled":
            raise ApiConnectionConflictError("connection is disabled")
        if record.scope == "task_ephemeral":
            if (
                record.created_by != operator_id
                or record.conversation_id != conversation_id
                or record.task_id is not None
                or record.credentials_revoked_at is not None
                or task_ephemeral_credentials_expired(record.created_at)
            ):
                raise ApiConnectionConflictError(
                    "task-ephemeral connection cannot be rotated"
                )
            if public_configuration is None:
                raise ApiConnectionValidationError(
                    "complete DingTalk configuration is required"
                )
            if record.provider_id == "dingtalk":
                public_configuration = validate_new_task_configuration(
                    public_configuration
                )
        _validate_secret_shape(secret, self._manifest(record.provider_id))
        await self._secrets.rotate(
            tenant_id=tenant_id,
            connection_id=connection_id,
            payload=secret,
        )
        if public_configuration is not None:
            record.public_configuration = dict(public_configuration)
        record.capabilities = {}
        record.visibility_summary = {}
        record.state = "pending"
        record.last_tested_at = None
        record.last_safe_error_code = None
        record.updated_by = operator_id
        return _safe_view(record)

    async def delete(
        self,
        *,
        tenant_id: str,
        connection_id: UUID,
    ) -> None:
        record = await self._owned(connection_id, tenant_id)
        if await self._repository.has_bound_sources(connection_id, tenant_id):
            raise ApiConnectionConflictError(
                "connection is retained because synchronization evidence references it"
            )
        await self._secrets.delete(
            tenant_id=tenant_id,
            secret_ref=record.secret_ref,
        )
        await self._repository.delete(record)

    async def _owned(
        self,
        connection_id: UUID,
        tenant_id: str,
    ) -> ApiConnectionRecord:
        record = await self._repository.get_for_tenant(connection_id, tenant_id)
        if record is None:
            raise ApiConnectionNotFoundError("API connection not found")
        return record

    async def _owned_for_update(
        self,
        connection_id: UUID,
        tenant_id: str,
    ) -> ApiConnectionRecord:
        record = await self._repository.get_for_tenant_for_update(
            connection_id,
            tenant_id,
        )
        if record is None:
            raise ApiConnectionNotFoundError("API connection not found")
        return record

    def _manifest(self, provider_id: str) -> ProviderManifest:
        try:
            return self._registry.manifest(provider_id)
        except KeyError as error:
            raise ApiConnectionValidationError("API provider is not registered") from error


def _validate_secret_shape(
    secret: Mapping[str, str],
    manifest: ProviderManifest,
) -> None:
    if set(secret) != set(manifest.required_secret_fields) or any(
        not isinstance(value, str) or not value.strip() for value in secret.values()
    ):
        raise ApiConnectionValidationError(
            "connection secret does not match the provider credential schema"
        )


def _safe_view(record: ApiConnectionRecord) -> SafeApiConnection:
    public_configuration = dict(record.public_configuration)
    if record.provider_id == "dingtalk":
        public_configuration = redact_server_configuration(public_configuration)
    return SafeApiConnection(
        id=record.id,
        tenant_id=record.tenant_id,
        provider_id=record.provider_id,
        display_name=record.display_name,
        public_configuration=public_configuration,
        manifest_version=record.manifest_version,
        adapter_version=record.adapter_version,
        capabilities=dict(record.capabilities),
        visibility_summary=dict(record.visibility_summary),
        state=record.state,
        last_tested_at=record.last_tested_at,
        last_safe_error_code=record.last_safe_error_code,
    )
