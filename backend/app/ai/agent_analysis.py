"""Strict, server-owned validation for new Agent model batch output."""

from collections.abc import Mapping
from uuid import UUID

from pydantic import ValidationError

from app.schemas.agent_reconciliation import AgentFindingPayload


class AgentModelOutputError(ValueError):
    pass


_ALLOWED_OPERATIONS: dict[str, frozenset[str]] = {
    "target_extra": frozenset({"delete", "retain"}),
    "target_duplicate": frozenset({"delete", "retain"}),
    "target_missing": frozenset({"create", "retain"}),
    "field_difference": frozenset({"update", "retain"}),
    "authority_invalid": frozenset({"skip"}),
    "identity_conflict": frozenset({"update", "delete", "retain"}),
}


def validate_agent_model_output(
    output: Mapping[str, object],
    expected_work_item_ids: tuple[UUID, ...],
    *,
    authority_invalid_ids: set[UUID] | None = None,
    expected_kinds: Mapping[UUID, str] | None = None,
) -> tuple[AgentFindingPayload, ...]:
    raw_findings = output.get("findings")
    if not isinstance(raw_findings, list):
        raise AgentModelOutputError("model result must contain a findings list")
    try:
        findings = tuple(AgentFindingPayload.model_validate(value) for value in raw_findings)
    except ValidationError as error:
        raise AgentModelOutputError("model result violates the Agent finding contract") from error
    finding_ids = tuple(finding.work_item_id for finding in findings)
    if (
        len(set(finding_ids)) != len(finding_ids)
        or set(finding_ids) != set(expected_work_item_ids)
    ):
        raise AgentModelOutputError(
            "model result must contain exactly one finding per batch work item"
        )
    invalid_ids = authority_invalid_ids or set()
    for finding in findings:
        if expected_kinds is not None and finding.kind != expected_kinds.get(finding.work_item_id):
            raise AgentModelOutputError("model finding kind does not match persisted work")
        if finding.work_item_id in invalid_ids:
            if finding.kind != "authority_invalid" or any(
                solution.operation != "skip" for solution in finding.solutions
            ):
                raise AgentModelOutputError(
                    "invalid authoritative rows require a read-only authority-invalid solution"
                )
        allowed = _ALLOWED_OPERATIONS.get(finding.kind)
        if allowed is None or any(
            solution.operation not in allowed for solution in finding.solutions
        ):
            raise AgentModelOutputError(
                "model solution operation is incompatible with the persisted finding"
            )
    return findings
