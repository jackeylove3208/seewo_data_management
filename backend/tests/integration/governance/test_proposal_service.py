from uuid import uuid4

import pytest

from app.ai.analysis_service import AnalysisService
from app.core.security import OperatorContext
from app.governance.proposal_service import ProposalConflict, ProposalService
from app.schemas.differences import DifferenceType
from app.schemas.governance import RecommendedAction
from app.schemas.proposals import (
    CreateAIProposalRequest,
    CreateManualProposalRequest,
    ProposalSource,
    ProposalStatus,
)
from tests.integration.ai.test_analysis_service import AgentSpy, seed_difference

OPERATOR = OperatorContext(operator_id="operator-1", tenant_id="school-1")


async def analyzed_attribute(session):
    difference = await seed_difference(session, DifferenceType.ATTRIBUTE_CONFLICT)
    analysis = await AnalysisService(session, agent=AgentSpy(), operator=OPERATOR).analyze(
        difference.id
    )
    return difference, analysis


@pytest.mark.asyncio
async def test_ai_proposal_copies_persisted_option_and_becomes_pending_execution(session) -> None:
    difference, analysis = await analyzed_attribute(session)
    assert analysis.output is not None
    option_id = analysis.output.options[0].option_id
    service = ProposalService(session, operator=OPERATOR)

    preview = await service.preview_ai(
        difference.id,
        CreateAIProposalRequest(
            analysis_id=analysis.id,
            option_id=option_id,
            expected_difference_version=difference.version,
        ),
    )
    proposal = await service.confirm_ai(
        difference.id,
        CreateAIProposalRequest(
            analysis_id=analysis.id,
            option_id=option_id,
            expected_difference_version=difference.version,
        ),
    )

    assert preview.changes[0].after == "13800000000"
    assert proposal.proposal_source is ProposalSource.AI
    assert proposal.status is ProposalStatus.PENDING_EXECUTION
    assert proposal.changes == preview.changes
    assert proposal.created_by == "operator-1"


@pytest.mark.asyncio
async def test_manual_proposal_derives_before_values_and_does_not_mutate_target(session) -> None:
    difference, _analysis = await analyzed_attribute(session)
    service = ProposalService(session, operator=OPERATOR)
    request = CreateManualProposalRequest(
        expected_difference_version=difference.version,
        operation_type=RecommendedAction.UPDATE,
        target_entity_id=difference.evidence.target_entity_id,
        changes={"phone": "13700000000"},
        rationale="The school operator verified the current teacher record",
    )
    before_payload = dict(difference.evidence.target_payload or {})

    preview = await service.preview_manual(difference.id, request)
    proposal = await service.confirm_manual(difference.id, request)

    assert preview.changes[0].before == "13900000000"
    assert preview.changes[0].after == "13700000000"
    assert proposal.proposal_source is ProposalSource.OPERATOR
    assert difference.evidence.target_payload == before_payload


@pytest.mark.asyncio
async def test_manual_proposal_rejects_noop_stale_and_cross_tenant_changes(session) -> None:
    difference, _analysis = await analyzed_attribute(session)
    service = ProposalService(session, operator=OPERATOR)
    base = {
        "operation_type": RecommendedAction.UPDATE,
        "target_entity_id": difference.evidence.target_entity_id,
        "rationale": "The operator checked the authoritative school record",
    }

    with pytest.raises(ProposalConflict, match="no effective changes"):
        await service.preview_manual(
            difference.id,
            CreateManualProposalRequest(
                expected_difference_version=difference.version,
                changes={"phone": "13900000000"},
                **base,
            ),
        )
    with pytest.raises(ProposalConflict, match="difference version"):
        await service.preview_manual(
            difference.id,
            CreateManualProposalRequest(
                expected_difference_version=difference.version + 1,
                changes={"phone": "13700000000"},
                **base,
            ),
        )
    other_tenant = ProposalService(
        session,
        operator=OperatorContext(operator_id="other", tenant_id="other-school"),
    )
    with pytest.raises(LookupError, match="not found"):
        await other_tenant.preview_manual(
            difference.id,
            CreateManualProposalRequest(
                expected_difference_version=difference.version,
                changes={"phone": "13700000000"},
                **base,
            ),
        )


@pytest.mark.asyncio
async def test_revised_proposal_creates_an_immutable_supersession_chain(session) -> None:
    difference, analysis = await analyzed_attribute(session)
    assert analysis.output is not None
    service = ProposalService(session, operator=OPERATOR)
    first = await service.confirm_ai(
        difference.id,
        CreateAIProposalRequest(
            analysis_id=analysis.id,
            option_id=analysis.output.options[0].option_id,
            expected_difference_version=difference.version,
        ),
    )
    second = await service.confirm_manual(
        difference.id,
        CreateManualProposalRequest(
            expected_difference_version=difference.version,
            operation_type=RecommendedAction.UPDATE,
            target_entity_id=difference.evidence.target_entity_id,
            changes={"phone": "13600000000"},
            rationale="A second operator verification selected a corrected phone value",
        ),
    )

    assert first.proposal_version == 1
    assert second.proposal_version == 2
    assert second.supersedes_id == first.id
    assert first.status is ProposalStatus.PENDING_EXECUTION


@pytest.mark.asyncio
async def test_ai_proposal_rejects_unknown_analysis_or_option(session) -> None:
    difference, analysis = await analyzed_attribute(session)
    service = ProposalService(session, operator=OPERATOR)

    with pytest.raises(ProposalConflict, match="analysis"):
        await service.confirm_ai(
            difference.id,
            CreateAIProposalRequest(
                analysis_id=uuid4(),
                option_id="unknown",
                expected_difference_version=difference.version,
            ),
        )
    with pytest.raises(ProposalConflict, match="option"):
        await service.confirm_ai(
            difference.id,
            CreateAIProposalRequest(
                analysis_id=analysis.id,
                option_id="unknown",
                expected_difference_version=difference.version,
            ),
        )
