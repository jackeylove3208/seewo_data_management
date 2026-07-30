"""Strict contracts for the model-backed synchronization conversation."""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.agent_api import AgentEntityType


class ConversationAgentContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    conversation_id: UUID
    tenant_id: str = Field(min_length=1)
    message: str = Field(min_length=1, max_length=2000)
    history: tuple["ConversationHistoryMessage", ...] = ()
    available_source_refs: tuple[str, ...] = ()
    conversation_remote_csv_enabled: bool = False
    remote_link_candidates: tuple["ConversationLinkBoundaryCandidate", ...] = ()
    available_remote_sources: tuple["ConversationRemoteSource", ...] = ()
    available_database_connectors: tuple["ConversationDatabaseConnector", ...] = ()
    available_api_providers: tuple["ConversationApiProvider", ...] = ()
    available_api_connections: tuple["ConversationApiConnection", ...] = ()
    current_intent: dict[str, Any] = Field(default_factory=dict)
    active_task_id: UUID | None = None


class ConversationHistoryMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Literal["assistant", "user"]
    kind: Literal["normal", "guardrail", "error"] = "normal"
    text: str = Field(min_length=1)


class ConversationDatabaseConnector(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    connector_id: str = Field(min_length=1, max_length=128)
    dialect: Literal["mysql", "postgresql"]
    source_role: Literal["authoritative", "target"]


class ConversationApiProvider(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider_id: str = Field(min_length=1, max_length=64)
    supported_entities: tuple[AgentEntityType, ...]
    required_secret_fields: tuple[str, ...]


class ConversationApiConnection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    connection_id: UUID
    provider_id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=255)
    state: Literal["pending", "active", "invalid", "disabled"]
    capabilities: dict[str, bool] = Field(default_factory=dict)
    visibility_summary: dict[str, str | int | bool | None] = Field(
        default_factory=dict
    )
    last_safe_error_code: str | None = None


class ConversationRemoteSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    remote_source_id: UUID
    display_origin: str = Field(min_length=1, max_length=255)


class ConversationLinkBoundaryCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    start: int = Field(ge=0)
    end: int = Field(gt=0)
    display_url: str = Field(min_length=1, max_length=2048)
    trailing_text: str = Field(default="", max_length=160)

    @model_validator(mode="after")
    def validate_boundary(self) -> "ConversationLinkBoundaryCandidate":
        if self.end <= self.start:
            raise ValueError("remote link candidate end must be after start")
        return self


class ConversationAgentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal[
        "clarification",
        "intent_update",
        "start_confirmation",
        "api_configuration",
        "active_task_notice",
        "safe_failure",
    ]
    message_zh: str = Field(min_length=1, max_length=1000)
    title: str | None = Field(default=None, min_length=1, max_length=255)
    entity_types: tuple[AgentEntityType, ...] = ()
    source_ref: str | None = Field(default=None, min_length=1)
    target_ref: str | None = Field(default=None, min_length=1)
    source_configuration_id: str | None = Field(default=None, min_length=1)
    target_configuration_id: str | None = Field(default=None, min_length=1)
    api_provider_id: str | None = Field(default=None, min_length=1, max_length=64)
    source_api_connection_id: UUID | None = None
    remote_source_id: UUID | None = None
    remote_url_start: int | None = Field(default=None, ge=0)
    remote_url_end: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_remote_url_boundary(self) -> "ConversationAgentDecision":
        if (self.remote_url_start is None) != (self.remote_url_end is None):
            raise ValueError("remote URL boundary must include both start and end")
        return self
