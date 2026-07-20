from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas.governance import (
    CauseAnalysisV2,
    GovernanceOption,
    ProposedFieldChange,
    RecommendedAction,
    RiskLevel,
)
from app.schemas.proposals import (
    CreateManualProposalRequest,
    GovernanceProposal,
    ProposalSource,
    ProposalStatus,
)
from app.schemas.workflow import AnalysisProgress, WorkflowStage, WorkflowState, WorkflowStatus


def option(*, option_id: str = "option-1", recommended: bool = True) -> GovernanceOption:
    return GovernanceOption(
        option_id=option_id,
        operation_type=RecommendedAction.UPDATE,
        target_entity_id=uuid4(),
        proposed_changes=(
            ProposedFieldChange(field="phone", before="13000000000", after="13100000000"),
        ),
        rationale="Use the authoritative phone value",
        evidence_refs=("field:phone",),
        risk=RiskLevel.LOW,
        confidence=0.91,
        preconditions=("target version remains current",),
        recommended=recommended,
    )


def test_analysis_v2_requires_exactly_one_recommended_option() -> None:
    with pytest.raises(ValidationError, match="exactly one option"):
        CauseAnalysisV2(
            cause="Two safe updates are available",
            evidence_summary="Both values are supported by persisted evidence",
            manual_only=False,
            options=(
                option(option_id="option-1", recommended=True),
                option(option_id="option-2", recommended=True),
            ),
        )


def test_analysis_v2_manual_only_rejects_options() -> None:
    with pytest.raises(ValidationError, match="manual-only analysis cannot contain options"):
        CauseAnalysisV2(
            cause="Identity cannot be established",
            evidence_summary="Candidate scores are tied",
            manual_only=True,
            manual_reason="A human must verify the identity",
            options=(option(),),
        )


def test_analysis_v2_manual_only_requires_a_reason() -> None:
    with pytest.raises(ValidationError, match="manual reason"):
        CauseAnalysisV2(
            cause="Identity cannot be established",
            evidence_summary="Candidate scores are tied",
            manual_only=True,
            manual_reason="   ",
            options=(),
        )


def test_governance_option_rejects_manual_review_operation() -> None:
    with pytest.raises(ValidationError, match="manual review is not an executable option"):
        GovernanceOption.model_validate(
            {
                **option().model_dump(),
                "operation_type": RecommendedAction.MANUAL_REVIEW,
            }
        )


def test_analysis_progress_requires_consistent_counts() -> None:
    with pytest.raises(ValidationError, match="completed count"):
        AnalysisProgress(total=5, completed=4, succeeded=2, manual_review=1, failed=0)


def test_workflow_failed_state_requires_an_error() -> None:
    with pytest.raises(ValidationError, match="failed workflow state requires an error"):
        WorkflowState(
            stage=WorkflowStage.ANALYSIS,
            status=WorkflowStatus.FAILED,
            attempt=1,
        )


@pytest.mark.parametrize("field", ["id", "source_id", "snapshot_id", "tenant_id"])
def test_manual_proposal_rejects_protected_fields(field: str) -> None:
    with pytest.raises(ValidationError, match="protected field"):
        CreateManualProposalRequest(
            expected_difference_version=1,
            operation_type=RecommendedAction.UPDATE,
            target_entity_id=uuid4(),
            changes={field: "spoofed"},
            rationale="Correct the entity using verified school records",
        )


def test_manual_proposal_rejects_blank_rationale() -> None:
    with pytest.raises(ValidationError, match="rationale"):
        CreateManualProposalRequest(
            expected_difference_version=1,
            operation_type=RecommendedAction.UPDATE,
            target_entity_id=uuid4(),
            changes={"phone": "13100000000"},
            rationale="   ",
        )


def test_pending_proposal_preserves_operator_source_and_version() -> None:
    proposal = GovernanceProposal(
        id=uuid4(),
        task_id=uuid4(),
        tenant_id="school-1",
        difference_id=uuid4(),
        difference_version=2,
        analysis_id=uuid4(),
        analysis_version="analysis-v2",
        proposal_version=3,
        proposal_source=ProposalSource.OPERATOR,
        operation_type=RecommendedAction.UPDATE,
        target_entity_id=uuid4(),
        changes=(ProposedFieldChange(field="phone", before="130", after="131"),),
        rationale="Verified against the authoritative school record",
        evidence_refs=("field:phone",),
        risk=RiskLevel.LOW,
        created_by="operator-1",
        created_at=datetime.now(UTC),
        status=ProposalStatus.PENDING_EXECUTION,
    )

    assert proposal.proposal_source is ProposalSource.OPERATOR
    assert proposal.proposal_version == 3
    assert proposal.status is ProposalStatus.PENDING_EXECUTION
