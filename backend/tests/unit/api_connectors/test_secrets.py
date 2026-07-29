from uuid import UUID

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_connectors.secrets import (
    EncryptedDatabaseSecretStore,
    SecretReferenceError,
)
from app.models.api_connectors import ApiConnectionRecord, ApiConnectionSecretRecord


@pytest.fixture
def fernet_key() -> bytes:
    return Fernet.generate_key()


async def test_secret_store_round_trips_without_plaintext_in_database(
    session: AsyncSession,
    fernet_key: bytes,
) -> None:
    store = EncryptedDatabaseSecretStore(session, fernet_key=fernet_key)

    secret_ref = await store.put(
        tenant_id="school-1",
        payload={"app_key": "app", "app_secret": "secret"},
    )

    row = await session.scalar(select(ApiConnectionSecretRecord))
    assert row is not None
    assert b"secret" not in row.ciphertext
    assert b"app" not in row.ciphertext
    assert secret_ref == f"db-secret:{row.id}"
    assert await store.get(tenant_id="school-1", secret_ref=secret_ref) == {
        "app_key": "app",
        "app_secret": "secret",
    }


async def test_secret_store_rejects_cross_tenant_and_invalid_references(
    session: AsyncSession,
    fernet_key: bytes,
) -> None:
    store = EncryptedDatabaseSecretStore(session, fernet_key=fernet_key)
    secret_ref = await store.put(
        tenant_id="school-1",
        payload={"app_key": "app", "app_secret": "secret"},
    )

    with pytest.raises(SecretReferenceError, match="unavailable"):
        await store.get(tenant_id="school-2", secret_ref=secret_ref)
    with pytest.raises(SecretReferenceError, match="invalid"):
        await store.get(tenant_id="school-1", secret_ref="vault-secret:unknown")
    with pytest.raises(SecretReferenceError, match="invalid"):
        await store.get(tenant_id="school-1", secret_ref="db-secret:not-a-uuid")


async def test_secret_store_rejects_non_string_secret_values(
    session: AsyncSession,
    fernet_key: bytes,
) -> None:
    store = EncryptedDatabaseSecretStore(session, fernet_key=fernet_key)

    with pytest.raises(ValueError, match="string values"):
        await store.put(
            tenant_id="school-1",
            payload={"app_key": "app", "expires_in": 3600},  # type: ignore[dict-item]
        )


async def test_secret_rotation_updates_connection_and_revokes_old_reference(
    session: AsyncSession,
    fernet_key: bytes,
) -> None:
    store = EncryptedDatabaseSecretStore(session, fernet_key=fernet_key)
    old_ref = await store.put(
        tenant_id="school-1",
        payload={"app_key": "old-app", "app_secret": "old-secret"},
    )
    connection = ApiConnectionRecord(
        tenant_id="school-1",
        provider_id="dingtalk",
        display_name="钉钉通讯录",
        public_configuration={},
        secret_ref=old_ref,
        manifest_version="1.0.0",
        adapter_version="1.0.0",
        capabilities={},
        visibility_summary={},
        state="pending",
        created_by="operator-1",
        updated_by="operator-1",
    )
    session.add(connection)
    await session.flush()

    new_ref = await store.rotate(
        tenant_id="school-1",
        connection_id=connection.id,
        payload={"app_key": "new-app", "app_secret": "new-secret"},
    )

    await session.refresh(connection)
    assert new_ref != old_ref
    assert connection.secret_ref == new_ref
    assert await store.get(tenant_id="school-1", secret_ref=new_ref) == {
        "app_key": "new-app",
        "app_secret": "new-secret",
    }
    with pytest.raises(SecretReferenceError, match="unavailable"):
        await store.get(tenant_id="school-1", secret_ref=old_ref)
    assert UUID(new_ref.removeprefix("db-secret:"))


async def test_secret_rotation_rejects_cross_tenant_connection(
    session: AsyncSession,
    fernet_key: bytes,
) -> None:
    store = EncryptedDatabaseSecretStore(session, fernet_key=fernet_key)
    secret_ref = await store.put(
        tenant_id="school-1",
        payload={"app_key": "app", "app_secret": "secret"},
    )
    connection = ApiConnectionRecord(
        tenant_id="school-1",
        provider_id="dingtalk",
        display_name="钉钉通讯录",
        public_configuration={},
        secret_ref=secret_ref,
        manifest_version="1.0.0",
        adapter_version="1.0.0",
        capabilities={},
        visibility_summary={},
        state="pending",
        created_by="operator-1",
        updated_by="operator-1",
    )
    session.add(connection)
    await session.flush()

    with pytest.raises(SecretReferenceError, match="unavailable"):
        await store.rotate(
            tenant_id="school-2",
            connection_id=connection.id,
            payload={"app_key": "new-app", "app_secret": "new-secret"},
        )
