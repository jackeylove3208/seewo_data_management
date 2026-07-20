from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.canonical_entities import EntityType
from app.schemas.governance import ProposedFieldChange, RecommendedAction, RiskLevel

PROTECTED_MANUAL_FIELDS = frozenset(
    {
        "id",
        "tenant_id",
        "snapshot_id",
        "source_id",
        "source_role",
        "raw_row_number",
        "raw_payload",
        "created_at",
        "updated_at",
    }
)


class ProposalSource(StrEnum):
    AI = "ai"
    OPERATOR = "operator"


class ProposalStatus(StrEnum):
    PENDING_EXECUTION = "pending_execution"
    SUPERSEDED = "superseded"


class CreateAIProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    analysis_id: UUID
    option_id: str = Field(min_length=1, max_length=64)
    expected_difference_version: int = Field(ge=1)


class CreateManualProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    expected_difference_version: int = Field(ge=1)
    operation_type: RecommendedAction
    target_entity_id: UUID | None = None
    changes: dict[str, Any] = Field(min_length=1, max_length=64)
    rationale: str = Field(min_length=3, max_length=2000)

    @field_validator("rationale")
    @classmethod
    def reject_blank_rationale(cls, value: str) -> str:
        stripped = value.strip()
        if len(stripped) < 3:
            raise ValueError("manual proposal rationale must contain at least 3 characters")
        return stripped

    @field_validator("changes")
    @classmethod
    def reject_protected_fields(cls, value: dict[str, Any]) -> dict[str, Any]:
        protected = sorted(PROTECTED_MANUAL_FIELDS.intersection(value))
        if protected:
            raise ValueError(f"protected field cannot be changed: {', '.join(protected)}")
        return value

    @model_validator(mode="after")
    def reject_non_executable_operation(self) -> "CreateManualProposalRequest":
        if self.operation_type in {RecommendedAction.MANUAL_REVIEW, RecommendedAction.SKIP}:
            raise ValueError("manual proposal requires an executable operation")
        return self


class GovernanceProposalPreview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    difference_id: UUID
    difference_version: int = Field(ge=1)
    proposal_source: ProposalSource
    operation_type: RecommendedAction
    target_entity_id: UUID | None = None
    changes: tuple[ProposedFieldChange, ...]
    rationale: str = Field(min_length=3, max_length=2000)
    evidence_refs: tuple[str, ...] = ()
    risk: RiskLevel


class GovernanceProposal(GovernanceProposalPreview):
    id: UUID
    task_id: UUID
    tenant_id: str = Field(min_length=1, max_length=128)
    analysis_id: UUID
    analysis_version: str = Field(min_length=1, max_length=64)
    proposal_version: int = Field(ge=1)
    created_by: str = Field(min_length=1, max_length=128)
    created_at: datetime
    status: ProposalStatus
    supersedes_id: UUID | None = None


class EditorFieldType(StrEnum):
    TEXT = "text"
    EMAIL = "email"
    PHONE = "phone"
    STATUS = "status"
    RELATION = "relation"


class EntityEditorField(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=128)
    field_type: EditorFieldType
    required: bool = False


class EntityEditorSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entity_type: EntityType
    fields: tuple[EntityEditorField, ...]
