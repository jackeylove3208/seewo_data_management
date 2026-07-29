import json
from collections.abc import Mapping
from typing import NoReturn
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api_connectors import ApiConnectionRecord, ApiConnectionSecretRecord

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
            )
        )
        if connection is None:
            _unavailable_secret()
        old_secret = await self._owned_secret(
            tenant_id=validated_tenant_id,
            secret_ref=connection.secret_ref,
        )
        new_ref = await self.put(
            tenant_id=validated_tenant_id,
            payload=payload,
        )
        connection.secret_ref = new_ref
        await self._session.delete(old_secret)
        await self._session.flush()
        return new_ref

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
