"""Strict contracts for the model-backed synchronization conversation."""

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.agent_api import AgentEntityType


class ConversationAgentContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    conversation_id: UUID
    tenant_id: str = Field(min_length=1)
    message: str = Field(min_length=1, max_length=2000)
    history: tuple["ConversationHistoryMessage", ...] = ()
    available_source_refs: tuple[str, ...] = ()
    available_database_connectors: tuple["ConversationDatabaseConnector", ...] = ()
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


class ConversationAgentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal[
        "clarification",
        "intent_update",
        "start_confirmation",
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
