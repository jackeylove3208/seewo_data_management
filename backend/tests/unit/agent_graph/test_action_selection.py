import pytest

from app.agent_graph.actions import InvalidActionSet, build_allowed_action_set
from app.agent_graph.contracts import (
    AllowedActionV1,
    CandidateActionEvaluationV1,
    SingleActionReasonCode,
)


def _action(
    action_id: str,
    *,
    resource_id: str | None = None,
    successor: str | None = None,
) -> AllowedActionV1:
    return AllowedActionV1(
        action_id=action_id,
        kind="dispatch_sub_agent",
        sub_agent="source-inspection",
        resource_ids=(resource_id or f"resource:{action_id}",),
        required_evidence=("source-inspection-v1",),
        risk="low",
        requires_human=False,
        successor_node=successor or f"{action_id}_complete",
    )


def _passed(action: AllowedActionV1) -> CandidateActionEvaluationV1:
    return CandidateActionEvaluationV1(action=action, passed=True)


def _rejected(
    action: AllowedActionV1, *codes: str
) -> CandidateActionEvaluationV1:
    return CandidateActionEvaluationV1(
        action=action,
        passed=False,
        rejected_guard_codes=codes,
    )


def test_multiple_safe_candidates_are_all_exposed_and_rejections_are_audited() -> None:
    result = build_allowed_action_set(
        (
            _passed(_action("inspect_authority")),
            _passed(_action("inspect_target")),
            _rejected(_action("execute_changes"), "approval_missing"),
        )
    )

    assert tuple(action.action_id for action in result.allowed_actions) == (
        "inspect_authority",
        "inspect_target",
    )
    assert result.single_action_reason_code is None
    assert result.excluded_action_summaries[0].action_id == "execute_changes"
    assert result.action_set_hash.startswith("sha256:")


def test_action_set_hash_is_stable_for_the_same_ordered_candidates() -> None:
    candidates = (
        _passed(_action("inspect_authority")),
        _passed(_action("inspect_target")),
    )

    assert (
        build_allowed_action_set(candidates).action_set_hash
        == build_allowed_action_set(candidates).action_set_hash
    )


def test_semantic_alias_actions_cannot_fake_choice() -> None:
    with pytest.raises(InvalidActionSet, match="semantic alias"):
        build_allowed_action_set(
            (
                _passed(_action("alias-a", resource_id="same", successor="same")),
                _passed(_action("alias-b", resource_id="same", successor="same")),
            )
        )


def test_singleton_requires_an_allowed_server_reason() -> None:
    candidate = (_passed(_action("terminate_task")),)

    with pytest.raises(InvalidActionSet, match="single_action_reason_code"):
        build_allowed_action_set(candidate)

    result = build_allowed_action_set(
        candidate,
        single_action_reason_code=SingleActionReasonCode.TERMINATION_REQUESTED,
    )

    assert result.single_action_reason_code is SingleActionReasonCode.TERMINATION_REQUESTED


def test_multiple_actions_reject_a_singleton_reason() -> None:
    with pytest.raises(InvalidActionSet, match="multiple allowed actions"):
        build_allowed_action_set(
            (
                _passed(_action("inspect_authority")),
                _passed(_action("inspect_target")),
            ),
            single_action_reason_code=SingleActionReasonCode.ONLY_GUARD_SATISFIED,
        )


def test_no_safe_action_is_rejected() -> None:
    with pytest.raises(InvalidActionSet, match="no safe action"):
        build_allowed_action_set(
            (_rejected(_action("execute_changes"), "approval_missing"),)
        )

