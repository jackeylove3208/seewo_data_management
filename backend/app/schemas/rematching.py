import re
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.canonical_entities import EntityType

_CHINESE_TEXT = re.compile(r"[\u3400-\u9fff]")


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class KeyGroupPolicy(_Contract):
    """One AND group inside a versioned OR-of-AND matching policy."""

    name: str = Field(min_length=1, max_length=128)
    fields: tuple[str, ...] = Field(min_length=1)

    @field_validator("fields")
    @classmethod
    def validate_fields(cls, fields: tuple[str, ...]) -> tuple[str, ...]:
        if any(not field.strip() for field in fields):
            raise ValueError("key-group fields cannot be blank")
        if len(set(fields)) != len(fields):
            raise ValueError("key-group fields must be unique")
        return fields

    def is_complete(self, values: dict[str, str | None]) -> bool:
        for field in self.fields:
            value = values.get(field)
            if value is None or not value.strip():
                return False
        return True


class VersionedKeyPolicy(_Contract):
    version: str = Field(min_length=1, max_length=64)
    entity_type: EntityType
    groups: tuple[KeyGroupPolicy, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_group_names(self) -> "VersionedKeyPolicy":
        names = [group.name for group in self.groups]
        if len(set(names)) != len(names):
            raise ValueError("key-policy group keys must be unique")
        return self

    def complete_groups(self, values: dict[str, str | None]) -> tuple[KeyGroupPolicy, ...]:
        return tuple(group for group in self.groups if group.is_complete(values))


class TrustedSourceIdentifierPolicy(_Contract):
    version: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(min_length=1, max_length=128)
    entity_type: EntityType
    source_snapshot_id: UUID
    target_snapshot_id: UUID
    trusted: bool = False
    field: str = Field(default="source_id", min_length=1, max_length=128)

    @property
    def can_auto_match(self) -> bool:
        return self.trusted


class KeyFieldEvidence(_Contract):
    field: str = Field(min_length=1, max_length=128)
    source_value: str | None = Field(default=None, max_length=1024)
    target_value: str | None = Field(default=None, max_length=1024)
    matched: bool


class KeyGroupEvidence(_Contract):
    policy_version: str = Field(min_length=1, max_length=64)
    group_key: str = Field(min_length=1, max_length=128)
    required_fields: tuple[str, ...] = Field(min_length=1)
    fields: tuple[KeyFieldEvidence, ...]
    candidate_entity_ids: tuple[UUID, ...] = ()

    @model_validator(mode="after")
    def validate_unique_fields_and_candidates(self) -> "KeyGroupEvidence":
        field_names = [item.field for item in self.fields]
        if len(set(self.required_fields)) != len(self.required_fields):
            raise ValueError("required fields must be unique")
        if len(set(field_names)) != len(field_names):
            raise ValueError("key evidence fields must be unique")
        if not set(field_names).issubset(self.required_fields):
            raise ValueError("key evidence contains fields outside required fields")
        if len(set(self.candidate_entity_ids)) != len(self.candidate_entity_ids):
            raise ValueError("candidate entity IDs must be unique")
        return self

    @property
    def complete(self) -> bool:
        evidence_by_field = {item.field: item for item in self.fields}
        for field in self.required_fields:
            evidence = evidence_by_field.get(field)
            if (
                evidence is None
                or evidence.source_value is None
                or not evidence.source_value.strip()
                or evidence.target_value is None
                or not evidence.target_value.strip()
                or not evidence.matched
            ):
                return False
        return True

    @property
    def unique_target_id(self) -> UUID | None:
        if self.complete and len(self.candidate_entity_ids) == 1:
            return self.candidate_entity_ids[0]
        return None


class KeyPolicyEvidence(_Contract):
    policy_version: str = Field(min_length=1, max_length=64)
    groups: tuple[KeyGroupEvidence, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_groups(self) -> "KeyPolicyEvidence":
        names = [group.group_key for group in self.groups]
        if len(set(names)) != len(names):
            raise ValueError("key evidence group keys must be unique")
        if any(group.policy_version != self.policy_version for group in self.groups):
            raise ValueError("key evidence must use one policy version")
        return self

    @property
    def unique_target_id(self) -> UUID | None:
        complete_groups = tuple(group for group in self.groups if group.complete)
        contains_non_unique_group = any(
            len(group.candidate_entity_ids) != 1 for group in complete_groups
        )
        if not complete_groups or contains_non_unique_group:
            return None
        target_ids = {group.candidate_entity_ids[0] for group in complete_groups}
        return next(iter(target_ids)) if len(target_ids) == 1 else None

    @property
    def conflicting_target_ids(self) -> frozenset[UUID]:
        target_ids = frozenset(
            target_id
            for group in self.groups
            if group.complete
            for target_id in group.candidate_entity_ids
        )
        return target_ids if len(target_ids) > 1 else frozenset()


class CandidateRole(StrEnum):
    AUTHORITATIVE = "authoritative"
    TARGET = "target"


class CandidateEdge(_Contract):
    focal_entity_id: UUID
    focal_role: CandidateRole
    candidate_entity_id: UUID
    candidate_role: CandidateRole
    rank: int = Field(ge=1)
    vector_score: float | None = Field(default=None, ge=0, le=1)
    lexical_score: float | None = Field(default=None, ge=0, le=1)
    representation_version: str = Field(min_length=1, max_length=64)
    evidence: tuple[KeyFieldEvidence, ...] = ()

    @model_validator(mode="after")
    def validate_roles(self) -> "CandidateEdge":
        if self.focal_role is self.candidate_role:
            raise ValueError("candidate edges require opposite source roles")
        if self.focal_entity_id == self.candidate_entity_id:
            raise ValueError("candidate edge endpoints must differ")
        return self


class _Decision(_Contract):
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("reason")
    @classmethod
    def require_chinese_reason(cls, reason: str) -> str:
        if _CHINESE_TEXT.search(reason) is None:
            raise ValueError("decision reason must contain Chinese business text")
        return reason


class AcceptCandidateDecision(_Decision):
    decision: Literal["accept_candidate"] = "accept_candidate"
    candidate_entity_id: UUID
    strong_evidence_features: tuple[str, ...] = Field(min_length=2)

    @field_validator("strong_evidence_features")
    @classmethod
    def validate_strong_features(cls, features: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(features)) < 2:
            raise ValueError("accept-candidate requires two distinct strong evidence features")
        return features


class NoMatchDecision(_Decision):
    decision: Literal["no_match"] = "no_match"


class ManualReviewDecision(_Decision):
    decision: Literal["manual_review"] = "manual_review"


RematchDecision = Annotated[
    AcceptCandidateDecision | NoMatchDecision | ManualReviewDecision,
    Field(discriminator="decision"),
]


class RematchDecisionRequest(_Contract):
    focal_entity_id: UUID
    server_candidate_ids: tuple[UUID, ...] = Field(min_length=1)
    decision: RematchDecision

    @model_validator(mode="after")
    def restrict_decision_to_candidates(self) -> "RematchDecisionRequest":
        if len(set(self.server_candidate_ids)) != len(self.server_candidate_ids):
            raise ValueError("server-owned candidate IDs must be unique")
        if isinstance(self.decision, AcceptCandidateDecision) and (
            self.decision.candidate_entity_id not in self.server_candidate_ids
        ):
            raise ValueError("accepted ID must belong to the server-owned candidate set")
        return self


class RematchingJobProgress(_Contract):
    initial_unresolved: int = Field(ge=0)
    indexed: int = Field(ge=0)
    processed: int = Field(ge=0)
    ai_recovered: int = Field(ge=0)
    no_match: int = Field(ge=0)
    manual_review: int = Field(ge=0)
    conflict: int = Field(ge=0)
    failed: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_counts(self) -> "RematchingJobProgress":
        terminal = (
            self.ai_recovered + self.no_match + self.manual_review + self.conflict + self.failed
        )
        if self.processed != terminal:
            raise ValueError("processed count must equal terminal outcome counts")
        if self.processed > self.indexed:
            raise ValueError("processed count cannot exceed indexed count")
        if self.indexed > self.initial_unresolved:
            raise ValueError("indexed count cannot exceed initial unresolved count")
        return self

    @property
    def remaining(self) -> int:
        return self.initial_unresolved - self.processed


class MatchingQualityCounts(_Contract):
    total: int = Field(ge=0)
    accepted: int = Field(ge=0)
    deterministic: int = Field(ge=0)
    ai_recovered: int = Field(ge=0)
    manual_review: int = Field(ge=0)
    conflict: int = Field(ge=0)
    unmatched: int = Field(ge=0)
    unconsumed_target: int = Field(ge=0)
    predicted_missing: int = Field(ge=0)
    predicted_redundant: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_partition(self) -> "MatchingQualityCounts":
        if self.accepted != self.deterministic + self.ai_recovered:
            raise ValueError("accepted count must equal deterministic and AI-recovered counts")
        if self.total != self.accepted + self.manual_review + self.conflict + self.unmatched:
            raise ValueError("total count must equal mapping outcome counts")
        if self.predicted_missing != self.unmatched:
            raise ValueError("predicted-missing count must equal unmatched count")
        if self.predicted_redundant != self.unconsumed_target:
            raise ValueError("predicted-redundant count must equal unconsumed-target count")
        return self

    @property
    def remaining_unresolved(self) -> int:
        return self.manual_review + self.conflict + self.unmatched

    @property
    def unresolved_ratio(self) -> float:
        return self.remaining_unresolved / self.total if self.total else 0.0


class MatchingQualityGate(_Contract):
    code: Literal["matching_quality_gate_failed"] = "matching_quality_gate_failed"
    affected_entity_types: tuple[EntityType, ...] = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=1000)
    observed_value: float = Field(ge=0)
    threshold: float = Field(ge=0)
    recovery_actions: tuple[str, ...] = Field(min_length=1)

    @field_validator("reason")
    @classmethod
    def require_chinese_reason(cls, reason: str) -> str:
        if _CHINESE_TEXT.search(reason) is None:
            raise ValueError("quality-gate reason must contain Chinese business text")
        return reason

    @field_validator("recovery_actions")
    @classmethod
    def require_chinese_actions(cls, actions: tuple[str, ...]) -> tuple[str, ...]:
        if any(_CHINESE_TEXT.search(action) is None for action in actions):
            raise ValueError("recovery actions must contain Chinese business text")
        return actions


class MatchingQualityResult(_Contract):
    task_id: UUID
    policy_version: str = Field(min_length=1, max_length=64)
    mapping_versions: tuple[str, ...] = Field(min_length=1)
    counts: dict[EntityType, MatchingQualityCounts]
    passed: bool
    failures: tuple[MatchingQualityGate, ...] = ()

    @model_validator(mode="after")
    def validate_gate_outcome(self) -> "MatchingQualityResult":
        if self.passed and self.failures:
            raise ValueError("passed result cannot contain gate failures")
        if not self.passed and not self.failures:
            raise ValueError("failed result requires at least one gate failure")
        return self

    @property
    def retryable(self) -> bool:
        return not self.passed
