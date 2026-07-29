from app.api_connectors.contracts import OrganizationApiAdapter, ProviderManifest


class ProviderRegistry:
    """In-memory allow-list binding audited manifests to backend-owned Adapters."""

    def __init__(self) -> None:
        self._providers: dict[str, tuple[ProviderManifest, OrganizationApiAdapter]] = {}

    def register(
        self,
        manifest: ProviderManifest,
        adapter: OrganizationApiAdapter,
    ) -> None:
        provider_id = manifest.provider_id
        if provider_id in self._providers:
            raise ValueError(f"provider {provider_id!r} is already registered")
        if adapter.manifest != manifest:
            raise ValueError("Adapter manifest does not match the registered provider manifest")
        self._providers[provider_id] = (manifest, adapter)

    def manifest(self, provider_id: str) -> ProviderManifest:
        return self._resolve(provider_id)[0]

    def adapter(self, provider_id: str) -> OrganizationApiAdapter:
        return self._resolve(provider_id)[1]

    def provider_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers))

    def _resolve(
        self,
        provider_id: str,
    ) -> tuple[ProviderManifest, OrganizationApiAdapter]:
        try:
            return self._providers[provider_id]
        except KeyError as error:
            raise KeyError(f"provider {provider_id!r} is not registered") from error


def build_default_provider_registry(
    *,
    dingtalk_adapter: OrganizationApiAdapter,
    wecom_adapter: OrganizationApiAdapter,
) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(dingtalk_adapter.manifest, dingtalk_adapter)
    registry.register(wecom_adapter.manifest, wecom_adapter)
    return registry
