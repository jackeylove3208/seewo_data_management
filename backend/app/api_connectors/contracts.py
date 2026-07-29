from collections.abc import AsyncIterator, Mapping
from datetime import datetime
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.agent_ingestion import AgentContractRecord, AgentEntityKind


class ProviderManifest(BaseModel):
    """Immutable, audited contract for one provider Adapter implementation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    manifest_version: str = Field(min_length=1, max_length=64)
    adapter_version: str = Field(min_length=1, max_length=64)
    supported_entities: frozenset[AgentEntityKind] = Field(min_length=1)
    required_secret_fields: tuple[str, ...] = Field(min_length=1)
    required_capabilities: tuple[str, ...] = Field(min_length=1)
    endpoint_hosts: tuple[str, ...] = Field(min_length=1)
    maximum_pages: int = Field(gt=0, le=100_000)
    projection_version: str = Field(min_length=1, max_length=64)

    @field_validator(
        "required_secret_fields",
        "required_capabilities",
        "endpoint_hosts",
    )
    @classmethod
    def _require_unique_non_blank_values(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        normalized = tuple(value.strip() for value in values)
        if any(not value for value in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError("provider manifest values must be unique and non-blank")
        return normalized

    @field_validator("endpoint_hosts")
    @classmethod
    def _require_fixed_endpoint_hosts(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(
            value != value.casefold()
            or "://" in value
            or "/" in value
            or "@" in value
            or any(character.isspace() for character in value)
            for value in values
        ):
            raise ValueError("provider endpoint hosts must be fixed lowercase host names")
        return values


class SafeApiConnection(BaseModel):
    """Connection view that is safe for APIs, Graph state, and model context."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: UUID
    tenant_id: str = Field(min_length=1, max_length=128)
    provider_id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=255)
    public_configuration: dict[str, object] = Field(default_factory=dict)
    manifest_version: str = Field(min_length=1, max_length=64)
    adapter_version: str = Field(min_length=1, max_length=64)
    capabilities: dict[str, bool] = Field(default_factory=dict)
    visibility_summary: dict[str, str | int | bool | None] = Field(default_factory=dict)
    state: str = Field(pattern=r"^(pending|active|invalid|disabled)$")
    secret_configured: bool
    last_tested_at: datetime | None = None
    last_safe_error_code: str | None = Field(default=None, max_length=128)

    @field_validator("public_configuration")
    @classmethod
    def _reject_secret_bearing_configuration(
        cls,
        value: dict[str, object],
    ) -> dict[str, object]:
        _validate_safe_configuration(value)
        return value


class ConnectionTestResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    eligible: bool
    capabilities: dict[str, bool] = Field(default_factory=dict)
    visibility_summary: dict[str, str | int | bool | None] = Field(default_factory=dict)
    safe_error_code: str | None = Field(default=None, max_length=128)


class FrozenApiRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    external_id: str = Field(min_length=1, max_length=512)
    entity_kind: AgentEntityKind
    provider_fields: dict[str, str | int | bool | None] = Field(default_factory=dict)
    unavailable_fields: tuple[str, ...] = ()


class CapturedApiPage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    page_number: int = Field(ge=1)
    records: tuple[FrozenApiRecord, ...]
    next_cursor: str | None = Field(default=None, max_length=2048)


class CaptureResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    records: tuple[FrozenApiRecord, ...]
    page_count: int = Field(ge=0)
    record_count: int = Field(ge=0)


class AgentProjectionContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: UUID
    run_id: UUID
    snapshot_id: UUID
    tenant_id: str = Field(min_length=1, max_length=128)
    connection_id: UUID
    stable_order: int = Field(ge=1)


class OrganizationApiAdapter(Protocol):
    """Deterministic provider boundary; secrets never cross beyond this protocol."""

    manifest: ProviderManifest

    async def test_connection(
        self,
        public_configuration: Mapping[str, object],
        secret: Mapping[str, str],
    ) -> ConnectionTestResult: ...

    def capture(
        self,
        public_configuration: Mapping[str, object],
        secret: Mapping[str, str],
        selected_entities: frozenset[AgentEntityKind],
    ) -> AsyncIterator[CapturedApiPage]: ...

    def project(
        self,
        record: FrozenApiRecord,
        context: AgentProjectionContext,
    ) -> AgentContractRecord: ...


def _validate_safe_configuration(value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized_key = key.casefold().replace("-", "_")
            if any(
                marker in normalized_key
                for marker in ("credential", "password", "secret", "token")
            ):
                raise ValueError("public configuration contains a secret-bearing field")
            _validate_safe_configuration(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _validate_safe_configuration(item)
