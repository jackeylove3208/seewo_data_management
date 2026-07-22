import re
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AgentEntityKind(StrEnum):
    DEPARTMENT = "department"
    STUDENT = "student"
    TEACHER = "teacher"


class AgentSourceRole(StrEnum):
    AUTHORITATIVE = "authoritative"
    TARGET = "target"


class AgentContractRecord(BaseModel):
    """Immutable six-field projection used exclusively by new Agent tasks."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: UUID
    run_id: UUID
    snapshot_id: UUID
    tenant_id: str = Field(min_length=1, max_length=128)
    source_role: AgentSourceRole
    stable_locator: str = Field(min_length=1, max_length=512)
    stable_order: int = Field(ge=1)
    entity_kind: AgentEntityKind
    category: str | None = Field(default=None, max_length=255)
    name: str | None = Field(default=None, max_length=255)
    number: str | None = Field(default=None, max_length=255)
    class_name: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=320)
    raw_row_number: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _class_applies_only_to_students(self) -> "AgentContractRecord":
        if self.entity_kind is not AgentEntityKind.STUDENT and self.class_name is not None:
            raise ValueError("class_name applies only to student records")
        return self


class AgentInputMark(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input_record_id: UUID
    reason_code: str = Field(min_length=1, max_length=128)
    affected_fields: tuple[str, ...] = ()
    inclusion_state: str = Field(pattern="^(included|excluded|anomaly)$")
    report_disposition: str = Field(min_length=1, max_length=64)
    safe_evidence: dict[str, str | None] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _reject_sensitive_evidence(self) -> "AgentInputMark":
        sensitive_keys = {"phone", "raw_phone", "original_phone", "original_value"}
        if sensitive_keys.intersection(key.casefold() for key in self.safe_evidence):
            raise ValueError("safe_evidence contains a sensitive field")
        if any(
            value is not None and re.search(r"(?<!\d)1\d{10}(?!\d)", value)
            for value in self.safe_evidence.values()
        ):
            raise ValueError("safe_evidence contains a sensitive phone value")
        return self
