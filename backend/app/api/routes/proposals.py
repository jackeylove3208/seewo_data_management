from collections.abc import Awaitable, Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_operator_context, get_session
from app.core.security import OperatorContext
from app.governance.batch_service import BatchConflict, BatchGovernanceService
from app.governance.field_policy import editor_schema
from app.governance.proposal_service import ProposalConflict, ProposalService
from app.repositories.differences import DifferenceRepository
from app.repositories.proposals import ProposalRepository
from app.schemas.batch_governance import (
    BatchPreviewRequest,
    BatchProposalPreview,
    BatchProposalResult,
    ConfirmBatchProposalRequest,
    TaskAnalysisSummary,
)
from app.schemas.canonical_entities import EntityType
from app.schemas.proposals import (
    CreateAIProposalRequest,
    CreateManualProposalRequest,
    EntityEditorSchema,
    GovernanceProposal,
    GovernanceProposalPreview,
)

router = APIRouter(prefix="/api", tags=["governance-proposals"])


def batch_service_for(
    request: Request,
    session: AsyncSession,
    operator: OperatorContext,
) -> BatchGovernanceService:
    return BatchGovernanceService(
        session,
        operator=operator,
        signing_secret=request.app.state.proposal_preview_secret,
    )


@router.get(
    "/reconciliation-tasks/{task_id}/analysis-summary",
    response_model=TaskAnalysisSummary,
)
async def get_analysis_summary(
    task_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> TaskAnalysisSummary:
    try:
        return await batch_service_for(request, session, operator).summary(task_id)
    except LookupError as error:
        raise HTTPException(404, detail=str(error)) from error


@router.post(
    "/reconciliation-tasks/{task_id}/proposal-batches/preview",
    response_model=BatchProposalPreview,
)
async def preview_proposal_batch(
    task_id: UUID,
    body: BatchPreviewRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> BatchProposalPreview:
    try:
        return await batch_service_for(request, session, operator).preview(task_id, body)
    except LookupError as error:
        raise HTTPException(404, detail=str(error)) from error
    except BatchConflict as error:
        raise HTTPException(409, detail=str(error)) from error


@router.post(
    "/reconciliation-tasks/{task_id}/proposal-batches/confirm",
    response_model=BatchProposalResult,
    status_code=status.HTTP_201_CREATED,
)
async def confirm_proposal_batch(
    task_id: UUID,
    body: ConfirmBatchProposalRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> BatchProposalResult:
    try:
        return await batch_service_for(request, session, operator).confirm(task_id, body)
    except LookupError as error:
        raise HTTPException(404, detail=str(error)) from error
    except BatchConflict as error:
        raise HTTPException(409, detail=str(error)) from error


@router.get("/entity-editor-schemas/{entity_type}", response_model=EntityEditorSchema)
async def get_editor_schema(entity_type: EntityType) -> EntityEditorSchema:
    return editor_schema(entity_type)


@router.post(
    "/differences/{difference_id}/proposals/from-analysis/preview",
    response_model=GovernanceProposalPreview,
)
async def preview_ai_proposal(
    difference_id: UUID,
    body: CreateAIProposalRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> GovernanceProposalPreview:
    return await _call(ProposalService(session, operator=operator).preview_ai, difference_id, body)


@router.post(
    "/differences/{difference_id}/proposals/from-analysis",
    response_model=GovernanceProposal,
    status_code=status.HTTP_201_CREATED,
)
async def confirm_ai_proposal(
    difference_id: UUID,
    body: CreateAIProposalRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> GovernanceProposal:
    return await _call(ProposalService(session, operator=operator).confirm_ai, difference_id, body)


@router.post(
    "/differences/{difference_id}/proposals/manual/preview",
    response_model=GovernanceProposalPreview,
)
async def preview_manual_proposal(
    difference_id: UUID,
    body: CreateManualProposalRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> GovernanceProposalPreview:
    return await _call(
        ProposalService(session, operator=operator).preview_manual,
        difference_id,
        body,
    )


@router.post(
    "/differences/{difference_id}/proposals/manual",
    response_model=GovernanceProposal,
    status_code=status.HTTP_201_CREATED,
)
async def confirm_manual_proposal(
    difference_id: UUID,
    body: CreateManualProposalRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> GovernanceProposal:
    return await _call(
        ProposalService(session, operator=operator).confirm_manual,
        difference_id,
        body,
    )


@router.get(
    "/differences/{difference_id}/proposals",
    response_model=list[GovernanceProposal],
)
async def list_proposals(
    difference_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> tuple[GovernanceProposal, ...]:
    difference = await DifferenceRepository(session).get(difference_id)
    if difference is None or difference.tenant_id != operator.tenant_id:
        raise HTTPException(404, detail="difference not found")
    return await ProposalRepository(session).list_for_difference(difference_id)


@router.get("/proposals/{proposal_id}", response_model=GovernanceProposal)
async def get_proposal(
    proposal_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> GovernanceProposal:
    proposal = await ProposalRepository(session).get(proposal_id)
    if proposal is None or proposal.tenant_id != operator.tenant_id:
        raise HTTPException(404, detail="proposal not found")
    return proposal


async def _call[
    RequestT: (CreateAIProposalRequest, CreateManualProposalRequest),
    ResponseT: (GovernanceProposalPreview, GovernanceProposal),
](
    method: Callable[[UUID, RequestT], Awaitable[ResponseT]],
    difference_id: UUID,
    body: RequestT,
) -> ResponseT:
    try:
        return await method(difference_id, body)
    except LookupError as error:
        raise HTTPException(404, detail=str(error)) from error
    except ProposalConflict as error:
        raise HTTPException(409, detail=str(error)) from error
