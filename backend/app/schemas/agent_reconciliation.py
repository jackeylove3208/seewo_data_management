from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class IdentityKeyKind(StrEnum):
    NUMBER = "number"
    PHONE = "phone"
    EMAIL = "email"


class WorkItemKind(StrEnum):
    RESOLVED = "resolved"
    IDENTITY_CONFLICT = "identity_conflict"
    TARGET_EXTRA = "target_extra"
    TARGET_DUPLICATE = "target_duplicate"
    TARGET_MISSING = "target_missing"
    FIELD_DIFFERENCE = "field_difference"
    CORRECT = "correct"


class WorkItemState(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    AWAITING_CLARIFICATION = "awaiting_clarification"
    ANALYZED = "analyzed"
    BLOCKED = "blocked"


class AgentSolutionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: str = Field(pattern="^(create|update|delete|retain|skip)$")
    risk: str = Field(pattern="^(low|medium|high)$")
    solution_zh: str = Field(min_length=1, max_length=4000)
    dependency_finding_ids: tuple[UUID, ...] = ()


class AgentFindingPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    work_item_id: UUID
    kind: str = Field(
        pattern="^(target_extra|target_duplicate|target_missing|field_difference|clarify)$"
    )
    category_zh: str = Field(min_length=1, max_length=255)
    analysis_zh: str = Field(min_length=1, max_length=8000)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=50)
    solutions: tuple[AgentSolutionPayload, ...] = Field(min_length=1, max_length=3)
