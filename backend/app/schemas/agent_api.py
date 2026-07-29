from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.agent_runtime.state_machine import AgentPhase


class AgentEntityType(StrEnum):
    DEPARTMENT = "department"
    STUDENT = "student"
    TEACHER = "teacher"


class AgentConnectorSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["csv", "api", "database", "local", "remote_csv"]
    upload_id: UUID | None = None
    configuration_id: str | None = Field(default=None, min_length=1, max_length=128)
    source_ref: str | None = Field(default=None, min_length=1, max_length=512)
    remote_source_id: UUID | None = None

    @model_validator(mode="after")
    def validate_reference(self) -> "AgentConnectorSelection":
        if self.kind == "csv":
            if (
                self.upload_id is None
                or self.configuration_id is not None
                or self.source_ref is not None
                or self.remote_source_id is not None
            ):
                raise ValueError("CSV connector requires only upload_id")
        elif self.kind == "local":
            if (
                self.source_ref is None
                or self.upload_id is not None
                or self.configuration_id is not None
                or self.remote_source_id is not None
            ):
                raise ValueError("local connector requires only source_ref")
        elif self.kind == "remote_csv":
            if (
                self.remote_source_id is None
                or self.upload_id is not None
                or self.configuration_id is not None
                or self.source_ref is not None
            ):
                raise ValueError("remote CSV connector requires only remote_source_id")
        elif (
            self.configuration_id is None
            or self.upload_id is not None
            or self.source_ref is not None
            or self.remote_source_id is not None
        ):
            raise ValueError("configured connector requires only configuration_id")
        return self


class AgentTaskIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=255)
    entity_types: frozenset[AgentEntityType] = Field(min_length=1)
    source: AgentConnectorSelection
    target: AgentConnectorSelection


class AgentConversationResponse(BaseModel):
    id: UUID
    status: Literal["active", "closed"]


class AgentConversationMessageView(BaseModel):
    id: UUID
    role: Literal["assistant", "user"]
    kind: Literal["normal", "guardrail", "error"]
    text: str
    created_at: datetime


class AgentMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=2000)


class AgentConnectorView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["csv", "api", "database", "local", "remote_csv"]
    upload_id: UUID | None = None
    configuration_id: str | None = None
    source_ref: str | None = None
    remote_source_id: UUID | None = None
    display_origin: str | None = None


class AgentIntentView(BaseModel):
    title: str
    entity_types: tuple[AgentEntityType, ...]
    source: AgentConnectorView | None = None
    target: AgentConnectorView | None = None


class AgentStartConfirmation(BaseModel):
    title: str
    summary: str
    entity_types: tuple[AgentEntityType, ...]


class AgentMessageResponse(BaseModel):
    accepted_message: str
    message: str
    intent: AgentIntentView
    start_confirmation: AgentStartConfirmation | None = None


class AgentTaskResponse(BaseModel):
    id: UUID
    workflow_version: Literal["new-agent-v1", "agent-graph-v1"]
    task_kind: Literal["sync", "rollback"]
    parent_task_id: UUID | None = None
    phase: AgentPhase
    status: str
    title: str | None = None
    report_id: UUID | None = None
    rollback_eligible: bool = False
    rollback_blocked_reason: Literal["already_rolled_back"] | None = None
    deletion_eligible: bool = True
    error: dict[str, object] | None = None


class AgentConversationCurrentResponse(AgentConversationResponse):
    messages: tuple[AgentConversationMessageView, ...]
    intent: AgentIntentView | None = None
    start_confirmation: AgentStartConfirmation | None = None
    task: AgentTaskResponse | None = None


class AgentTaskEventResponse(BaseModel):
    id: UUID
    cursor: str
    type: str
    phase: AgentPhase | None = None
    status: str | None = None
    payload: dict[str, Any]
    created_at: datetime


class AgentEventPage(BaseModel):
    cursor: str
    events: tuple[AgentTaskEventResponse, ...]


class AgentCommandResponse(BaseModel):
    status: str


class AgentLocalSourceView(BaseModel):
    source_ref: str
    kind: Literal["csv"] = "csv"
    writable_as_target: bool


class AgentActiveLockResponse(BaseModel):
    active: bool
    owner_task_id: UUID | None = None
    owner_run_id: UUID | None = None
    acquired_at: datetime | None = None
    heartbeat_at: datetime | None = None


class AgentHistoryTargetSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=96)
    name: str = Field(min_length=1, max_length=255)
    kind: Literal["database", "local", "upload", "unknown"]
    identified: bool


class AgentHistoryItem(AgentTaskResponse):
    created_at: datetime
    completed_at: datetime | None = None
    termination_requested: bool = False
    issue_summary: dict[str, int]
    operation_summary: dict[str, int]
    rollback_eligible: bool
    deletion_eligible: bool
    entity_types: tuple[AgentEntityType, ...]
    target_source: AgentHistoryTargetSource


class AgentHistoryPage(BaseModel):
    items: tuple[AgentHistoryItem, ...]
    next_cursor: str | None = None


class ApprovalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=1000)


class ClarificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=500)


class StructuredClarificationSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["select_candidate", "treat_as_extra"]
    selected_candidate_id: UUID | None = None
    note: str | None = Field(default=None, max_length=500)
    graph_cursor: int = Field(ge=0)
    idempotency_key: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_selection(self) -> "StructuredClarificationSelectionRequest":
        self.note = self.note.strip() or None if self.note is not None else None
        self.idempotency_key = self.idempotency_key.strip()
        if not self.idempotency_key:
            raise ValueError("idempotency_key must not be blank")
        if self.decision == "select_candidate" and self.selected_candidate_id is None:
            raise ValueError("selected_candidate_id is required when selecting a candidate")
        if self.decision == "treat_as_extra" and self.selected_candidate_id is not None:
            raise ValueError("selected_candidate_id is not allowed for target extra")
        return self


class ClarificationConfirmationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed: bool = True


class AgentRollbackPreviewResponse(BaseModel):
    task_id: UUID
    source_task_id: UUID
    target_version_id: UUID
    operation_count: int
    state: Literal["awaiting_confirmation", "in_progress", "completed", "ended"]
    message_zh: str
    requires_confirmation: bool


class AgentApprovalGroupView(BaseModel):
    id: UUID
    status: str
    issue_kind: str
    entity_kind: str
    operation: str
    item_count: int


class AgentClarificationView(BaseModel):
    id: UUID
    status: str
    masked_candidates: tuple[dict[str, Any], ...]
    allowed_outcomes: tuple[str, ...]


class AgentInteractionResponse(BaseModel):
    approval_groups: tuple[AgentApprovalGroupView, ...]
    clarifications: tuple[AgentClarificationView, ...]


class AgentReportResponse(BaseModel):
    id: UUID
    task_id: UUID
    kind: str
    terminal_state: str
    facts: dict[str, Any]
    content: dict[str, Any]
    rollback_eligible: bool
    deletion_eligible: bool
    created_at: datetime
