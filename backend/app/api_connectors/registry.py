import httpx

from app.api_connectors.contracts import OrganizationApiAdapter, ProviderManifest
from app.api_connectors.dingtalk import DingtalkOrganizationAdapter
from app.api_connectors.wecom import WeComOrganizationAdapter


class ProviderRegistry:
    """In-memory allow-list binding audited manifests to backend-owned Adapters."""

    def __init__(self) -> None:
        self._providers: dict[
            tuple[str, str, str],
            tuple[ProviderManifest, OrganizationApiAdapter],
        ] = {}
        self._current_versions: dict[str, tuple[str, str]] = {}

    def register(
        self,
        manifest: ProviderManifest,
        adapter: OrganizationApiAdapter,
        *,
        make_current: bool | None = None,
    ) -> None:
        if adapter.manifest != manifest:
            raise ValueError("Adapter manifest does not match the registered provider manifest")
        key = (
            manifest.provider_id,
            manifest.manifest_version,
            manifest.adapter_version,
        )
        if key in self._providers:
            raise ValueError(f"provider version {key!r} is already registered")
        self._providers[key] = (manifest, adapter)
        if make_current is True or manifest.provider_id not in self._current_versions:
            self._current_versions[manifest.provider_id] = (
                manifest.manifest_version,
                manifest.adapter_version,
            )

    def manifest(
        self,
        provider_id: str,
        *,
        manifest_version: str | None = None,
        adapter_version: str | None = None,
    ) -> ProviderManifest:
        return self.resolve(
            provider_id,
            manifest_version=manifest_version,
            adapter_version=adapter_version,
        )[0]

    def adapter(
        self,
        provider_id: str,
        *,
        manifest_version: str | None = None,
        adapter_version: str | None = None,
    ) -> OrganizationApiAdapter:
        return self.resolve(
            provider_id,
            manifest_version=manifest_version,
            adapter_version=adapter_version,
        )[1]

    def provider_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._current_versions))

    def resolve(
        self,
        provider_id: str,
        *,
        manifest_version: str | None,
        adapter_version: str | None,
    ) -> tuple[ProviderManifest, OrganizationApiAdapter]:
        if (manifest_version is None) != (adapter_version is None):
            raise KeyError("provider version requires manifest and Adapter versions")
        if manifest_version is None:
            try:
                manifest_version, adapter_version = self._current_versions[provider_id]
            except KeyError as error:
                raise KeyError(f"provider {provider_id!r} is not registered") from error
        assert adapter_version is not None
        key = (provider_id, manifest_version, adapter_version)
        try:
            return self._providers[key]
        except KeyError as error:
            raise KeyError(f"provider version {key!r} is not registered") from error

def build_default_provider_runtime(
    *,
    connect_timeout: float,
    read_timeout: float,
) -> tuple[ProviderRegistry, tuple[httpx.AsyncClient, ...]]:
    timeout = httpx.Timeout(read_timeout, connect=connect_timeout)
    clients = (
        httpx.AsyncClient(timeout=timeout, follow_redirects=False),
        httpx.AsyncClient(timeout=timeout, follow_redirects=False),
    )
    dingtalk = DingtalkOrganizationAdapter(clients[0])
    wecom = WeComOrganizationAdapter(clients[1])
    registry = ProviderRegistry()
    registry.register(dingtalk.manifest, dingtalk)
    registry.register(wecom.manifest, wecom)
    return registry, clients
