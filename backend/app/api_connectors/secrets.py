import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import NoReturn
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_connectors import (
    ApiAuthoritySourceRecord,
    ApiConnectionRecord,
    ApiConnectionSecretRecord,
)

_SECRET_REFERENCE_PREFIX = "db-secret:"


class SecretReferenceError(ValueError):
    """Safe failure for malformed, cross-tenant, missing, or unreadable secrets."""


class EncryptedDatabaseSecretStore:
    """Tenant-bound Fernet storage available only to backend provider runtimes."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        fernet_key: bytes | str | SecretStr,
        key_version: str = "fernet-v1",
    ) -> None:
        self._session = session
        self._fernet = _build_fernet(fernet_key)
        self._key_version = key_version

    async def put(
        self,
        *,
        tenant_id: str,
        payload: Mapping[str, str],
    ) -> str:
        serialized = _serialize_secret_payload(payload)
        record = ApiConnectionSecretRecord(
            tenant_id=_validated_tenant_id(tenant_id),
            ciphertext=self._fernet.encrypt(serialized),
            key_version=self._key_version,
        )
        self._session.add(record)
        await self._session.flush()
        return f"{_SECRET_REFERENCE_PREFIX}{record.id}"

    async def get(
        self,
        *,
        tenant_id: str,
        secret_ref: str,
    ) -> dict[str, str]:
        record = await self._owned_secret(
            tenant_id=_validated_tenant_id(tenant_id),
            secret_ref=secret_ref,
        )
        try:
            plaintext = self._fernet.decrypt(record.ciphertext)
        except InvalidToken:
            _unavailable_secret()
        return _deserialize_secret_payload(plaintext)

    async def rotate(
        self,
        *,
        tenant_id: str,
        connection_id: UUID,
        payload: Mapping[str, str],
    ) -> str:
        validated_tenant_id = _validated_tenant_id(tenant_id)
        connection = await self._session.scalar(
            select(ApiConnectionRecord).where(
                ApiConnectionRecord.id == connection_id,
                ApiConnectionRecord.tenant_id == validated_tenant_id,
            ).with_for_update()
        )
        if connection is None:
            _unavailable_secret()
        old_secret_ref = connection.secret_ref
        await self._owned_secret(
            tenant_id=validated_tenant_id,
            secret_ref=old_secret_ref,
        )
        new_ref = await self.put(
            tenant_id=validated_tenant_id,
            payload=payload,
        )
        connection.secret_ref = new_ref
        await self._session.flush()
        await delete_unreferenced_secret(
            self._session,
            tenant_id=validated_tenant_id,
            secret_ref=old_secret_ref,
        )
        await self._session.flush()
        return new_ref

    async def delete(
        self,
        *,
        tenant_id: str,
        secret_ref: str,
    ) -> None:
        record = await self._owned_secret(
            tenant_id=_validated_tenant_id(tenant_id),
            secret_ref=secret_ref,
        )
        await self._session.delete(record)
        await self._session.flush()

    async def _owned_secret(
        self,
        *,
        tenant_id: str,
        secret_ref: str,
    ) -> ApiConnectionSecretRecord:
        secret_id = _parse_secret_reference(secret_ref)
        record = await self._session.scalar(
            select(ApiConnectionSecretRecord).where(
                ApiConnectionSecretRecord.id == secret_id,
                ApiConnectionSecretRecord.tenant_id == tenant_id,
            )
        )
        if record is None:
            _unavailable_secret()
        return record


async def delete_unreferenced_secret(
    session: AsyncSession,
    *,
    tenant_id: str,
    secret_ref: str,
) -> None:
    """Delete a stored secret after its last connection or frozen task releases it."""
    validated_tenant_id = _validated_tenant_id(tenant_id)
    secret = await session.scalar(
        select(ApiConnectionSecretRecord)
        .where(
            ApiConnectionSecretRecord.id == _parse_secret_reference(secret_ref),
            ApiConnectionSecretRecord.tenant_id == validated_tenant_id,
        )
        .with_for_update()
    )
    if secret is None:
        return
    connection_reference, source_reference = (
        await session.execute(
            select(
                exists().where(
                    ApiConnectionRecord.tenant_id == validated_tenant_id,
                    ApiConnectionRecord.secret_ref == secret_ref,
                ),
                exists().where(
                    ApiAuthoritySourceRecord.tenant_id == validated_tenant_id,
                    ApiAuthoritySourceRecord.frozen_secret_ref == secret_ref,
                ),
            )
        )
    ).one()
    if connection_reference or source_reference:
        return
    await session.delete(secret)


async def revoke_ephemeral_connection(
    session: AsyncSession,
    *,
    tenant_id: str,
    connection_id: UUID,
    reason: str,
    expected_conversation_id: UUID | None = None,
    require_unbound: bool = False,
) -> bool:
    allowed_reasons = {
        "snapshot_materialized",
        "conversation_reset",
        "task_terminated",
        "task_failed",
        "superseded",
        "configuration_expired",
    }
    if reason not in allowed_reasons:
        raise ValueError("ephemeral connection revocation reason is invalid")
    connection = await session.scalar(
        select(ApiConnectionRecord)
        .where(
            ApiConnectionRecord.id == connection_id,
            ApiConnectionRecord.tenant_id == _validated_tenant_id(tenant_id),
        )
        .with_for_update()
    )
    if (
        connection is None
        or connection.scope != "task_ephemeral"
        or connection.credentials_revoked_at is not None
        or (
            expected_conversation_id is not None
            and connection.conversation_id != expected_conversation_id
        )
        or (require_unbound and connection.task_id is not None)
    ):
        return False
    secret_refs = {
        connection.secret_ref,
        *(
            await session.scalars(
                select(ApiAuthoritySourceRecord.frozen_secret_ref).where(
                    ApiAuthoritySourceRecord.tenant_id == tenant_id,
                    ApiAuthoritySourceRecord.connection_id == connection.id,
                )
            )
        ),
    }
    for secret_ref in secret_refs:
        secret = await session.scalar(
            select(ApiConnectionSecretRecord).where(
                ApiConnectionSecretRecord.id == _parse_secret_reference(secret_ref),
                ApiConnectionSecretRecord.tenant_id == tenant_id,
            )
        )
        if secret is not None:
            await session.delete(secret)
    connection.state = "disabled"
    connection.credentials_revoked_at = datetime.now(UTC)
    connection.disabled_reason = reason
    await session.flush()
    return True


async def revoke_task_ephemeral_connection(
    session: AsyncSession,
    *,
    tenant_id: str,
    task_id: UUID,
    reason: str,
) -> None:
    connection_id = await session.scalar(
        select(ApiConnectionRecord.id).where(
            ApiConnectionRecord.tenant_id == _validated_tenant_id(tenant_id),
            ApiConnectionRecord.task_id == task_id,
            ApiConnectionRecord.scope == "task_ephemeral",
        )
    )
    if connection_id is None:
        return
    await revoke_ephemeral_connection(
        session,
        tenant_id=tenant_id,
        connection_id=connection_id,
        reason=reason,
    )


async def revoke_conversation_ephemeral_connections(
    session: AsyncSession,
    *,
    tenant_id: str,
    conversation_id: UUID,
    reason: str,
) -> None:
    connection_ids = tuple(
        await session.scalars(
            select(ApiConnectionRecord.id).where(
                ApiConnectionRecord.tenant_id == _validated_tenant_id(tenant_id),
                ApiConnectionRecord.conversation_id == conversation_id,
                ApiConnectionRecord.scope == "task_ephemeral",
                ApiConnectionRecord.task_id.is_(None),
            )
        )
    )
    for connection_id in connection_ids:
        await revoke_ephemeral_connection(
            session,
            tenant_id=tenant_id,
            connection_id=connection_id,
            reason=reason,
            expected_conversation_id=conversation_id,
            require_unbound=True,
        )


async def revoke_expired_ephemeral_connections(
    session: AsyncSession,
    *,
    tenant_id: str,
    expires_before: datetime,
) -> int:
    connection_ids = tuple(
        await session.scalars(
            select(ApiConnectionRecord.id).where(
                ApiConnectionRecord.tenant_id == _validated_tenant_id(tenant_id),
                ApiConnectionRecord.provider_id == "dingtalk",
                ApiConnectionRecord.scope == "task_ephemeral",
                ApiConnectionRecord.task_id.is_(None),
                ApiConnectionRecord.credentials_revoked_at.is_(None),
                ApiConnectionRecord.created_at < expires_before,
            )
        )
    )
    revoked = 0
    for connection_id in connection_ids:
        if await revoke_ephemeral_connection(
            session,
            tenant_id=tenant_id,
            connection_id=connection_id,
            reason="configuration_expired",
            require_unbound=True,
        ):
            revoked += 1
    return revoked


def _build_fernet(value: bytes | str | SecretStr) -> Fernet:
    raw_value = value.get_secret_value() if isinstance(value, SecretStr) else value
    encoded = raw_value.encode() if isinstance(raw_value, str) else raw_value
    try:
        return Fernet(encoded)
    except (TypeError, ValueError) as error:
        raise ValueError("API connector secret key must be a valid Fernet key") from error


def _validated_tenant_id(tenant_id: str) -> str:
    stripped = tenant_id.strip()
    if not stripped:
        raise ValueError("tenant_id must be non-blank")
    return stripped


def _serialize_secret_payload(payload: Mapping[str, str]) -> bytes:
    if (
        not payload
        or any(not isinstance(key, str) or not key for key in payload)
        or any(not isinstance(value, str) for value in payload.values())
    ):
        raise ValueError("secret payload must contain non-empty string keys and string values")
    return json.dumps(
        dict(payload),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _deserialize_secret_payload(plaintext: bytes) -> dict[str, str]:
    try:
        value = json.loads(plaintext)
    except (UnicodeDecodeError, json.JSONDecodeError):
        _unavailable_secret()
    if (
        not isinstance(value, dict)
        or not value
        or any(not isinstance(key, str) or not key for key in value)
        or any(not isinstance(item, str) for item in value.values())
    ):
        _unavailable_secret()
    return value


def _parse_secret_reference(secret_ref: str) -> UUID:
    if not secret_ref.startswith(_SECRET_REFERENCE_PREFIX):
        raise SecretReferenceError("secret reference is invalid")
    try:
        return UUID(secret_ref.removeprefix(_SECRET_REFERENCE_PREFIX))
    except ValueError as error:
        raise SecretReferenceError("secret reference is invalid") from error


def _unavailable_secret() -> NoReturn:
    raise SecretReferenceError("secret reference is unavailable")
