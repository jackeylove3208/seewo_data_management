from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.agent_ingestion import AgentEntityKind


class ApiProviderRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str
    manifest_version: str
    adapter_version: str
    supported_entities: tuple[AgentEntityKind, ...]
    required_secret_fields: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    projection_version: str


class ApiConfigurationSessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str = Field(min_length=1, max_length=64)
    conversation_id: UUID | None = None


class ApiConfigurationSessionRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    provider_id: str
    required_secret_fields: tuple[str, ...]
    expires_at: datetime


class ApiConnectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    configuration_session_id: UUID
    provider_id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=255)
    public_configuration: dict[str, object] = Field(default_factory=dict)
    secret: dict[str, str]


class ApiConnectionRotateSecret(BaseModel):
    model_config = ConfigDict(extra="forbid")

    public_configuration: dict[str, object] | None = None
    secret: dict[str, str]


class ApiConnectionRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    tenant_id: str
    provider_id: str
    display_name: str
    public_configuration: dict[str, object]
    manifest_version: str
    adapter_version: str
    capabilities: dict[str, bool]
    visibility_summary: dict[str, str | int | bool | None]
    state: str
    last_tested_at: datetime | None
    last_safe_error_code: str | None


class ExternalIdentityBindingConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    connection_id: UUID
    entity_kind: AgentEntityKind
    authority_stable_locator: str = Field(min_length=1, max_length=512)
    target_connector_id: str = Field(min_length=1, max_length=128)
    target_stable_locator: str = Field(min_length=1, max_length=512)


class ExternalIdentityBindingRead(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: UUID
    tenant_id: str
    provider_id: str
    connection_id: UUID
    entity_kind: AgentEntityKind
    authority_stable_locator: str
    target_connector_id: str
    target_stable_locator: str
    status: str
    binding_version: int
    confirmed_by: str
    confirmed_at: datetime
    revoked_by: str | None
    revoked_at: datetime | None
    evidence_hash: str

    @field_validator("confirmed_at", "revoked_at", mode="before")
    @classmethod
    def normalize_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
