from datetime import UTC, datetime, timedelta

from cryptography.fernet import Fernet

from app.api_connectors.maintenance import ApiConnectorCredentialMaintenanceWorker
from app.api_connectors.secrets import EncryptedDatabaseSecretStore
from app.models.agent_runtime import AgentConversationRecord
from app.models.api_connectors import ApiConnectionRecord


async def test_maintenance_worker_revokes_expired_unbound_dingtalk_connection(
    database,
) -> None:
    now = datetime.now(UTC)
    key = Fernet.generate_key()
    async with database.session_factory() as session:
        async with session.begin():
            conversation = AgentConversationRecord(
                tenant_id="school-1",
                created_by="operator-1",
                status="active",
                context={},
            )
            session.add(conversation)
            await session.flush()
            secret_ref = await EncryptedDatabaseSecretStore(
                session,
                fernet_key=key,
            ).put(
                tenant_id="school-1",
                payload={"app_key": "app", "app_secret": "secret"},
            )
            connection = ApiConnectionRecord(
                tenant_id="school-1",
                provider_id="dingtalk",
                display_name="已过期临时连接",
                scope="task_ephemeral",
                conversation_id=conversation.id,
                public_configuration={
                    "person_entity_kind": "teacher",
                    "root_department_id": 1,
                },
                secret_ref=secret_ref,
                manifest_version="v1",
                adapter_version="v1",
                capabilities={},
                visibility_summary={},
                state="active",
                created_at=now - timedelta(hours=25),
                created_by="operator-1",
                updated_by="operator-1",
            )
            session.add(connection)
            await session.flush()
            connection_id = connection.id

    worker = ApiConnectorCredentialMaintenanceWorker(
        database.session_factory,
        now=lambda: now,
    )
    assert await worker.run_once() is True

    async with database.session_factory() as session:
        connection = await session.get(ApiConnectionRecord, connection_id)
        assert connection is not None
        assert connection.state == "disabled"
        assert connection.disabled_reason == "configuration_expired"
        assert connection.credentials_revoked_at is not None
