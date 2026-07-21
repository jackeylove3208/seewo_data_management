from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agent import GovernanceAgent
from app.ai.analysis_service import AnalysisService
from app.ai.mcp.server import MCPToolGateway
from app.ai.providers.llm import HttpLLMProvider
from app.api.dependencies import get_operator_context, get_session
from app.core.security import OperatorContext
from app.repositories.analyses import (
    ANALYSIS_V3_VERSION,
    CURRENT_ANALYSIS_VERSION,
    AnalysisRepository,
)
from app.repositories.differences import DifferenceRepository
from app.schemas.governance import AnalysisJobResponse, AnalysisResult

router = APIRouter(prefix="/api", tags=["analyses"])


def service_for(
    request: Request,
    session: AsyncSession,
    operator: OperatorContext,
) -> AnalysisService:
    settings = request.app.state.settings
    tokenization_secret = (
        settings.tokenization_secret.get_secret_value()
        if settings.tokenization_secret is not None
        else None
    )
    agent = GovernanceAgent(
        HttpLLMProvider(settings=settings),
        MCPToolGateway(session),
        tokenization_secret=tokenization_secret,
    )
    return AnalysisService(
        session,
        agent=agent,
        operator=operator,
    )


@router.post(
    "/reconciliation-tasks/{task_id}/analyses",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AnalysisJobResponse,
)
async def analyze_task(
    task_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> AnalysisJobResponse:
    try:
        return await service_for(request, session, operator).analyze_task(task_id)
    except LookupError as error:
        raise HTTPException(404, detail=str(error)) from error


@router.get("/differences/{difference_id}/analysis", response_model=AnalysisResult)
async def get_analysis(
    difference_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> AnalysisResult:
    difference = await DifferenceRepository(session).get(difference_id)
    if difference is None or difference.tenant_id != operator.tenant_id:
        raise HTTPException(404, detail="analysis not found")
    repository = AnalysisRepository(session)
    result = await repository.get_for_difference(
        difference.id,
        difference.version,
        ANALYSIS_V3_VERSION,
    )
    if result is None:
        result = await repository.get_for_difference(
            difference.id,
            difference.version,
            CURRENT_ANALYSIS_VERSION,
        )
    if result is None:
        raise HTTPException(404, detail="analysis not found")
    return result
