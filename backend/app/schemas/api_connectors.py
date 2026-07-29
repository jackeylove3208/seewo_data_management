from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

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
