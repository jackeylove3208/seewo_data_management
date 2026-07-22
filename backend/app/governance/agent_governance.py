"""Server-owned governance contracts for ``new-agent-v1`` CSV runs.

This module deliberately does not depend on legacy Difference/Proposal records.  It
turns the immutable analysis findings from Conversation 2 into bounded approvals,
plans, and typed target operations.  Persistence adapters can serialize these
contracts without allowing model output to become workflow truth.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from uuid import NAMESPACE_URL, UUID, uuid5


class ClarificationError(ValueError):
    pass


class AgentOperation(str):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    RETAIN = "retain"
    SKIP = "skip"

    def __new__(cls, value: str) -> "AgentOperation":
        if value not in {cls.CREATE, cls.UPDATE, cls.DELETE, cls.RETAIN, cls.SKIP}:
            raise ValueError(f"unsupported Agent operation: {value}")
        return str.__new__(cls, value)


@dataclass(frozen=True)
class AgentFindingInput:
    finding_id: UUID
    work_item_id: UUID
    entity_kind: str
    kind: str
    operation: AgentOperation
    changed_fields: frozenset[str]
    before: Mapping[str, object] | None
    after: Mapping[str, object] | None
    target_source_identifier: str | None
    dependencies: frozenset[UUID]
    analysis_terminal: bool
    target_version: str


@dataclass(frozen=True)
class AgentRiskDecision:
    risk: str
    policy_version: str
    requires_approval: bool


class AgentRiskPolicy:
    """Versioned, server-owned policy; model-provided risk is ignored."""

    version = "agent-risk-v1"

    def assess(self, finding: AgentFindingInput) -> AgentRiskDecision:
        high = finding.operation == AgentOperation.DELETE or (
            finding.entity_kind == "student" and bool({"phone"} & finding.changed_fields)
        )
        if high:
            risk = "high"
        elif finding.operation in {AgentOperation.RETAIN, AgentOperation.SKIP}:
            risk = "low"
        else:
            risk = "medium"
        return AgentRiskDecision(
            risk=risk,
            policy_version=self.version,
            requires_approval=risk == "high",
        )


@dataclass(frozen=True)
class AgentApprovalGroup:
    id: UUID
    finding_ids: tuple[UUID, ...]
    issue_kind: str
    entity_kind: str
    operation: AgentOperation
    policy_version: str
    membership_hash: str
    risk: str = "high"


def group_high_risk_findings(
    findings: tuple[AgentFindingInput, ...],
    *,
    policy: AgentRiskPolicy | None = None,
) -> tuple[AgentApprovalGroup, ...]:
    policy = policy or AgentRiskPolicy()
    grouped: dict[
        tuple[str, str, AgentOperation, str, tuple[str, ...]], list[AgentFindingInput]
    ] = {}
    for finding in findings:
        decision = policy.assess(finding)
        if not decision.requires_approval:
            continue
        key = (
            finding.kind,
            finding.entity_kind,
            finding.operation,
            decision.policy_version,
            tuple(sorted(finding.changed_fields)),
        )
        grouped.setdefault(key, []).append(finding)

    groups: list[AgentApprovalGroup] = []
    for key, members in sorted(grouped.items(), key=lambda item: str(item[0])):
        finding_ids = tuple(sorted((item.finding_id for item in members), key=str))
        membership_hash = sha256(
            (
                "|".join(str(item) for item in finding_ids) + ":" + ":".join((key[3], *key[4]))
            ).encode()
        ).hexdigest()
        group_id = uuid5(NAMESPACE_URL, f"agent-approval:{membership_hash}")
        groups.append(
            AgentApprovalGroup(
                id=group_id,
                finding_ids=finding_ids,
                issue_kind=key[0],
                entity_kind=key[1],
                operation=key[2],
                policy_version=key[3],
                membership_hash=membership_hash,
            )
        )
    return tuple(groups)


@dataclass(frozen=True)
class ClarificationDecision:
    work_item_id: UUID
    outcome: str
    candidate_id: UUID | None
    original_text: str
    confirmed: bool = False


def interpret_clarification(
    text: str,
    *,
    work_item_id: UUID | None = None,
    candidates: tuple[UUID, ...],
    allowed_outcomes: tuple[str, ...],
) -> ClarificationDecision:
    """Translate only an exact listed candidate/outcome; all other text is rejected."""

    normalized = text.strip()
    if not normalized or len(normalized) > 500:
        raise ClarificationError("clarification text is empty or too long")
    matched_candidates = tuple(
        candidate for candidate in candidates if str(candidate) in normalized
    )
    matched_outcomes = tuple(outcome for outcome in allowed_outcomes if outcome in normalized)
    if len(matched_candidates) > 1 or len(matched_outcomes) > 1:
        raise ClarificationError("clarification is ambiguous")
    if matched_candidates:
        outcome = "use_candidate"
        if outcome not in allowed_outcomes:
            raise ClarificationError("candidate selection is not allowed")
        return ClarificationDecision(
            work_item_id or UUID(int=0), outcome, matched_candidates[0], normalized
        )
    if matched_outcomes:
        return ClarificationDecision(
            work_item_id or UUID(int=0), matched_outcomes[0], None, normalized
        )
    raise ClarificationError("clarification does not match the frozen candidates or outcomes")


def confirm_clarification(
    decision: ClarificationDecision, *, confirmed: bool
) -> ClarificationDecision:
    if not confirmed:
        raise ClarificationError("second confirmation is required")
    return ClarificationDecision(
        work_item_id=decision.work_item_id,
        outcome=decision.outcome,
        candidate_id=decision.candidate_id,
        original_text=decision.original_text,
        confirmed=True,
    )


@dataclass(frozen=True)
class AgentGovernanceOperation:
    id: UUID
    finding_id: UUID
    operation: AgentOperation
    entity_kind: str
    target_source_identifier: str | None
    before: Mapping[str, object] | None
    after: Mapping[str, object] | None
    dependencies: frozenset[UUID]
    risk: str
    target_version: str


@dataclass(frozen=True)
class AgentGovernancePlan:
    id: UUID
    target_version: str
    operations: tuple[AgentGovernanceOperation, ...]


def compile_agent_plan(
    findings: tuple[AgentFindingInput, ...],
    *,
    approved_group_ids: frozenset[UUID],
    confirmed_conflicts: frozenset[UUID],
    policy: AgentRiskPolicy | None = None,
) -> AgentGovernancePlan:
    policy = policy or AgentRiskPolicy()
    if not findings:
        raise ValueError("no Agent findings were supplied")
    if any(not finding.analysis_terminal for finding in findings):
        raise ValueError("analysis is not terminal")
    groups = group_high_risk_findings(findings, policy=policy)
    approvals_by_finding = {
        finding_id: group.id for group in groups for finding_id in group.finding_ids
    }
    for finding in findings:
        if finding.kind == "identity_conflict" and finding.work_item_id not in confirmed_conflicts:
            continue
        group_id = approvals_by_finding.get(finding.finding_id)
        if group_id is not None and group_id not in approved_group_ids:
            raise ValueError("high-risk approval is required")

    target_versions = {finding.target_version for finding in findings}
    if len(target_versions) != 1:
        raise ValueError("all operations must use one frozen target version")
    executable_findings = [
        finding
        for finding in sorted(findings, key=lambda item: str(item.finding_id))
        if not (
            finding.kind == "identity_conflict" and finding.work_item_id not in confirmed_conflicts
        )
        and finding.operation not in {AgentOperation.RETAIN, AgentOperation.SKIP}
    ]
    operation_ids = {
        finding.finding_id: uuid5(
            NAMESPACE_URL, f"agent-operation:{finding.finding_id}:{finding.target_version}"
        )
        for finding in executable_findings
    }
    operations: list[AgentGovernanceOperation] = []
    for finding in executable_findings:
        if finding.kind == "identity_conflict" and finding.work_item_id not in confirmed_conflicts:
            continue
        operation = finding.operation
        if operation in {AgentOperation.RETAIN, AgentOperation.SKIP}:
            continue
        if operation != AgentOperation.CREATE and not finding.target_source_identifier:
            raise ValueError("target mutation requires a target source identifier")
        risk = policy.assess(finding).risk
        operation_id = operation_ids[finding.finding_id]
        dependencies = frozenset(
            operation_ids[dependency]
            for dependency in finding.dependencies
            if dependency in operation_ids
        )
        operations.append(
            AgentGovernanceOperation(
                id=operation_id,
                finding_id=finding.finding_id,
                operation=operation,
                entity_kind=finding.entity_kind,
                target_source_identifier=finding.target_source_identifier,
                before=finding.before,
                after=finding.after,
                dependencies=dependencies,
                risk=risk,
                target_version=finding.target_version,
            )
        )
    if not operations:
        raise ValueError("no executable Agent operations remain")
    target_version = next(iter(target_versions))
    plan_hash = sha256(
        repr(
            [
                (str(operation.id), operation.operation, sorted(operation.dependencies))
                for operation in operations
            ]
        ).encode()
    ).hexdigest()
    return AgentGovernancePlan(
        id=uuid5(NAMESPACE_URL, f"agent-plan:{target_version}:{plan_hash}"),
        target_version=target_version,
        operations=tuple(operations),
    )
