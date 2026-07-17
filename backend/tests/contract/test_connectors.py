from pathlib import Path
from uuid import uuid4

import pytest

from app.connectors.base import (
    ConnectorNotConfigured,
    ConnectorReadRequest,
    ConnectorVersion,
    SourceConnector,
)
from app.connectors.csv_source import ThirdPartyCsvConnector
from app.connectors.csv_target import MofaCsvConnector
from app.connectors.database import DatabaseSourceConnector
from app.connectors.registry import ConnectorRegistry
from app.connectors.seewo_api import SeewoApiConnector
from app.connectors.third_party_api import ThirdPartyApiConnector
from app.ingestion.field_mapping import default_mapping_registry
from app.schemas.canonical_entities import SourceRole

ROOT = Path(__file__).parents[3]


class FakeSourceConnector:
    async def version(self) -> ConnectorVersion:
        return ConnectorVersion(value="fixture-v1")

    async def read(self, request: ConnectorReadRequest):
        raise NotImplementedError


def test_source_protocol_is_runtime_checkable() -> None:
    assert isinstance(FakeSourceConnector(), SourceConnector)


def test_registry_returns_connector_by_name() -> None:
    registry = ConnectorRegistry()
    connector = FakeSourceConnector()
    registry.register("fixture", connector)

    assert registry.get("fixture") is connector
    with pytest.raises(LookupError, match="unknown connector"):
        registry.get("missing")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("connector_type", "filename", "profile_version", "role", "expected_count"),
    [
        (
            ThirdPartyCsvConnector,
            "third_party_data.csv",
            "third-party-v1",
            SourceRole.AUTHORITATIVE,
            515,
        ),
        (MofaCsvConnector, "mofa_data.csv", "mofa-v1", SourceRole.TARGET, 518),
    ],
)
async def test_csv_connectors_emit_same_canonical_contract(
    connector_type,
    filename: str,
    profile_version: str,
    role: SourceRole,
    expected_count: int,
) -> None:
    connector = connector_type(
        path=ROOT / filename,
        profile=default_mapping_registry().get(profile_version),
        tenant_id="school-1",
        snapshot_id=uuid4(),
    )

    result = await connector.read(ConnectorReadRequest())

    assert result.batch.source_role is role
    assert len(result.batch.entities) == expected_count
    assert (await connector.version()).value.startswith("sha256:")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "connector",
    [SeewoApiConnector(), ThirdPartyApiConnector(), DatabaseSourceConnector()],
)
async def test_future_connectors_fail_explicitly_when_not_configured(connector) -> None:
    with pytest.raises(ConnectorNotConfigured):
        await connector.version()
