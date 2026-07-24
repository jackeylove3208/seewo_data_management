import hashlib
import json
import re
from collections.abc import Iterable

from app.agent_graph.contracts import (
    AllowedActionSetV1,
    AllowedActionV1,
    CandidateActionEvaluationV1,
    ExcludedActionSummaryV1,
    SingleActionReasonCode,
    SupervisorContextV1,
    SupervisorDecisionV1,
)


class InvalidActionSet(ValueError):
    pass


class InvalidSupervisorDecision(ValueError):
    pass


def build_allowed_action_set(
    candidate_action_evaluations: Iterable[CandidateActionEvaluationV1],
    *,
    single_action_reason_code: SingleActionReasonCode | None = None,
) -> AllowedActionSetV1:
    evaluations = tuple(candidate_action_evaluations)
    action_ids = tuple(evaluation.action.action_id for evaluation in evaluations)
    if len(set(action_ids)) != len(action_ids):
        raise InvalidActionSet("candidate action IDs must be unique")

    allowed_actions = tuple(
        evaluation.action for evaluation in evaluations if evaluation.passed
    )
    if not allowed_actions:
        raise InvalidActionSet("no safe action is available")
    if len(allowed_actions) == 1 and single_action_reason_code is None:
        raise InvalidActionSet("single_action_reason_code is required")
    if len(allowed_actions) > 1 and single_action_reason_code is not None:
        raise InvalidActionSet("multiple allowed actions cannot use a singleton reason")

    fingerprints: dict[tuple[object, ...], str] = {}
    for action in allowed_actions:
        fingerprint = _semantic_fingerprint(action)
        previous = fingerprints.get(fingerprint)
        if previous is not None:
            raise InvalidActionSet(
                f"semantic alias actions are forbidden: {previous}, {action.action_id}"
            )
        fingerprints[fingerprint] = action.action_id

    excluded = tuple(
        ExcludedActionSummaryV1(
            action_id=evaluation.action.action_id,
            rejected_guard_codes=evaluation.rejected_guard_codes,
        )
        for evaluation in evaluations
        if not evaluation.passed
    )
    return AllowedActionSetV1(
        allowed_actions=allowed_actions,
        action_set_hash=_action_set_hash(allowed_actions),
        single_action_reason_code=single_action_reason_code,
        excluded_action_summaries=excluded,
    )


def validate_supervisor_decision(
    context: SupervisorContextV1,
    decision: SupervisorDecisionV1,
) -> SupervisorDecisionV1:
    actions = {action.action_id: action for action in context.allowed_actions}
    selected = actions.get(decision.action_id)
    if selected is None:
        raise InvalidSupervisorDecision(
            f"Supervisor action is not allowed: {decision.action_id}"
        )
    if decision.expected_result not in selected.required_evidence:
        raise InvalidSupervisorDecision("expected result is not produced by the selected action")
    unknown_blockers = set(decision.observed_blockers).difference(context.active_blockers)
    if unknown_blockers:
        raise InvalidSupervisorDecision(
            f"unknown blocker referenced: {sorted(unknown_blockers)[0]}"
        )

    reason_ids = tuple(item.action_id for item in decision.why_not_other_actions_zh)
    if len(set(reason_ids)) != len(reason_ids):
        raise InvalidSupervisorDecision("unselected action coverage contains duplicates")
    expected_reason_ids = set(actions).difference({decision.action_id})
    if set(reason_ids) != expected_reason_ids:
        raise InvalidSupervisorDecision("unselected action coverage is incomplete")
    if decision.operator_message_zh is None:
        return decision
    return decision.model_copy(
        update={
            "operator_message_zh": _sanitize_operator_message(
                decision.operator_message_zh
            )
        }
    )


_PHONE_PATTERN = re.compile(r"(?<!\d)(1\d{6})(\d{4})(?!\d)")
_INTERNAL_RESOURCE_PATTERN = re.compile(
    r"\b(?:operation|work-item|paired-record|execution-plan|manifest|snapshot)"
    r":[0-9a-fA-F-]{16,}\b"
)


def _sanitize_operator_message(message: str) -> str:
    masked = _PHONE_PATTERN.sub(lambda match: f"***{match.group(2)}", message)
    return _INTERNAL_RESOURCE_PATTERN.sub("[内部引用]", masked)


def _semantic_fingerprint(action: AllowedActionV1) -> tuple[object, ...]:
    return (
        action.kind,
        action.graph_action_kind,
        action.sub_agent,
        action.resource_ids,
        action.required_evidence,
        action.successor_node,
    )


def _action_set_hash(actions: tuple[AllowedActionV1, ...]) -> str:
    payload = [action.model_dump(mode="json") for action in actions]
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"
