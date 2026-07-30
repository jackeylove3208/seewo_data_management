from collections.abc import AsyncIterator, Mapping

import pytest

from app.api_connectors.contracts import (
    CapturedApiPage,
    ConnectionTestResult,
    ProviderManifest,
    SafeApiConnection,
)
from app.api_connectors.registry import ProviderRegistry
from app.schemas.agent_ingestion import AgentEntityKind

DINGTALK_MANIFEST = ProviderManifest(
    provider_id="dingtalk",
    manifest_version="1.0.0",
    adapter_version="1.0.0",
    supported_entities=frozenset(
        {
            AgentEntityKind.DEPARTMENT,
            AgentEntityKind.STUDENT,
            AgentEntityKind.TEACHER,
        }
    ),
    required_secret_fields=("app_key", "app_secret"),
    required_capabilities=("organization.read",),
    endpoint_hosts=("api.dingtalk.com",),
    maximum_pages=10_000,
    projection_version="organization-six-fields-v1",
)


class FakeAdapter:
    manifest = DINGTALK_MANIFEST

    async def test_connection(
        self,
        public_configuration: Mapping[str, object],
        secret: Mapping[str, str],
    ) -> ConnectionTestResult:
        del public_configuration, secret
        return ConnectionTestResult(
            eligible=True,
            capabilities={"organization.read": True},
            visibility_summary={"visible": True},
        )

    async def capture(
        self,
        public_configuration: Mapping[str, object],
        secret: Mapping[str, str],
        selected_entities: frozenset[AgentEntityKind],
    ) -> AsyncIterator[CapturedApiPage]:
        del public_configuration, secret, selected_entities
        if False:
            yield CapturedApiPage(page_number=1, records=(), next_cursor=None)


def test_registry_resolves_audited_manifest_and_adapter() -> None:
    adapter = FakeAdapter()
    registry = ProviderRegistry()

    registry.register(DINGTALK_MANIFEST, adapter)

    assert registry.manifest("dingtalk") is DINGTALK_MANIFEST
    assert registry.adapter("dingtalk") is adapter
    assert registry.provider_ids() == ("dingtalk",)


def test_registry_rejects_duplicate_provider() -> None:
    registry = ProviderRegistry()
    registry.register(DINGTALK_MANIFEST, FakeAdapter())

    with pytest.raises(ValueError, match="already registered"):
        registry.register(DINGTALK_MANIFEST, FakeAdapter())


def test_registry_resolves_frozen_adapter_version_after_a_new_version_is_current() -> None:
    old_adapter = FakeAdapter()
    new_manifest = DINGTALK_MANIFEST.model_copy(
        update={"manifest_version": "2.0.0", "adapter_version": "2.0.0"},
    )

    class NewAdapter(FakeAdapter):
        manifest = new_manifest

    new_adapter = NewAdapter()
    registry = ProviderRegistry()
    registry.register(DINGTALK_MANIFEST, old_adapter)
    registry.register(new_manifest, new_adapter, make_current=True)

    assert registry.manifest("dingtalk") is new_manifest
    assert registry.adapter("dingtalk") is new_adapter
    assert registry.adapter(
        "dingtalk",
        manifest_version="1.0.0",
        adapter_version="1.0.0",
    ) is old_adapter


def test_registry_does_not_silently_upgrade_an_unavailable_frozen_version() -> None:
    registry = ProviderRegistry()
    registry.register(DINGTALK_MANIFEST, FakeAdapter())

    with pytest.raises(KeyError, match="version"):
        registry.adapter(
            "dingtalk",
            manifest_version="0.9.0",
            adapter_version="0.9.0",
        )


def test_registry_rejects_adapter_manifest_mismatch() -> None:
    mismatched_manifest = DINGTALK_MANIFEST.model_copy(
        update={"provider_id": "wecom"},
    )
    registry = ProviderRegistry()

    with pytest.raises(ValueError, match="manifest"):
        registry.register(mismatched_manifest, FakeAdapter())


def test_registry_returns_only_known_provider_ids() -> None:
    registry = ProviderRegistry()

    with pytest.raises(KeyError, match="not registered"):
        registry.adapter("unregistered")


def test_safe_connection_rejects_secret_bearing_public_configuration() -> None:
    with pytest.raises(ValueError, match="secret"):
        SafeApiConnection(
            id="00000000-0000-0000-0000-000000000001",
            tenant_id="school-1",
            provider_id="dingtalk",
            display_name="钉钉通讯录",
            public_configuration={"app_secret": "must-not-leak"},
            manifest_version="1.0.0",
            adapter_version="1.0.0",
            capabilities={},
            visibility_summary={},
            state="pending",
        )
