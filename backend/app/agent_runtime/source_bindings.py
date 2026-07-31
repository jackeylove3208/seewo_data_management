import hashlib
import json
from dataclasses import dataclass
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.configured import DatabaseConnectorConfiguration
from app.models.api_connectors import AgentSourceBindingRecord

SourceRoleName = Literal["authoritative", "target"]
ConnectorKind = Literal["api", "database", "csv"]


@dataclass(frozen=True, slots=True)
class AgentSourceBinding:
    role: SourceRoleName
    connector_kind: ConnectorKind
    configuration_id: str
    frozen_public_configuration: dict[str, object]
    credential_reference: str
    mapping_checkpoint_key: str
    normalization_checkpoint_key: str

    def database_configuration(self) -> DatabaseConnectorConfiguration:
        if self.connector_kind != "database":
            raise ValueError("source binding is not a database connector")
        configuration = DatabaseConnectorConfiguration.model_validate(
            self.frozen_public_configuration
        )
        if configuration.credential_reference != self.credential_reference:
            raise ValueError("source binding credential reference changed")
        return configuration


async def load_source_bindings(
    session: AsyncSession,
    *,
    task_id: UUID,
    tenant_id: str,
) -> tuple[AgentSourceBinding, AgentSourceBinding]:
    records = tuple(
        await session.scalars(
            select(AgentSourceBindingRecord)
            .where(
                AgentSourceBindingRecord.task_id == task_id,
                AgentSourceBindingRecord.tenant_id == tenant_id,
            )
            .order_by(AgentSourceBindingRecord.role)
        )
    )
    bindings = tuple(_binding_from_record(record) for record in records)
    by_role = {binding.role: binding for binding in bindings}
    if len(bindings) != 2 or set(by_role) != {"authoritative", "target"}:
        raise ValueError("source role bindings are incomplete")
    return by_role["authoritative"], by_role["target"]


def _binding_from_record(record: AgentSourceBindingRecord) -> AgentSourceBinding:
    public_configuration = dict(record.frozen_public_configuration)
    if _configuration_fingerprint(public_configuration) != record.configuration_fingerprint:
        raise ValueError("source binding configuration fingerprint changed")
    return AgentSourceBinding(
        role=cast(SourceRoleName, record.role),
        connector_kind=cast(ConnectorKind, record.connector_kind),
        configuration_id=record.configuration_id,
        frozen_public_configuration=public_configuration,
        credential_reference=record.credential_reference,
        mapping_checkpoint_key=record.mapping_checkpoint_key,
        normalization_checkpoint_key=record.normalization_checkpoint_key,
    )


def _configuration_fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
