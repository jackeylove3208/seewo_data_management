from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

from app.schemas.executions import OperationType, json_values_equal
from app.schemas.governance import RiskLevel


class RiskAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    risk: RiskLevel
    requires_explicit_acknowledgement: bool
    reversible: bool

    @model_validator(mode="after")
    def validate_acknowledgement_requirement(self) -> "RiskAssessment":
        if self.requires_explicit_acknowledgement is not (self.risk is RiskLevel.HIGH):
            raise ValueError("explicit acknowledgement is required exactly for high risk")
        return self


def assess_operation(
    *,
    operation_type: OperationType,
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
    changed_fields: frozenset[str],
    has_dependents: bool,
) -> RiskAssessment:
    if _changed_fact_fields(before, after) != changed_fields:
        raise ValueError("changed_fields must exactly match the operation fact changes")

    if operation_type in {OperationType.MOVE, OperationType.DISABLE} or has_dependents:
        risk = RiskLevel.HIGH
    elif operation_type is OperationType.SKIP:
        risk = RiskLevel.LOW
    else:
        risk = RiskLevel.MEDIUM

    reversible = _is_reversible(
        operation_type=operation_type,
        before=before,
        after=after,
        changed_fields=changed_fields,
    )
    return RiskAssessment(
        risk=risk,
        requires_explicit_acknowledgement=risk is RiskLevel.HIGH,
        reversible=reversible,
    )


def _is_reversible(
    *,
    operation_type: OperationType,
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
    changed_fields: frozenset[str],
) -> bool:
    if operation_type is OperationType.SKIP:
        return False
    if operation_type is OperationType.CREATE:
        return after is not None
    if before is None or after is None or not changed_fields:
        return False
    return changed_fields <= before.keys()


def _changed_fact_fields(
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
) -> frozenset[str]:
    before_facts = before or {}
    after_facts = after or {}
    return frozenset(
        field
        for field in before_facts.keys() | after_facts.keys()
        if field not in before_facts
        or field not in after_facts
        or not json_values_equal(before_facts[field], after_facts[field])
    )
