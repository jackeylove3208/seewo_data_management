from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers.llm import HttpLLMProvider
from app.api.dependencies import get_operator_context, get_session
from app.core.security import OperatorContext
from app.executions.record_service import ExecutionRecordService
from app.models.reporting import GovernanceReportRecord, ReportJobRecord
from app.reports.service import ReportService
from app.repositories.reporting import ReportingRepository
from app.schemas.reporting import (
    ExecutionFactBundle,
    GovernanceReportContent,
    GovernanceReportResponse,
)

router = APIRouter(prefix="/api", tags=["reports"])


def _service(
    request: Request,
    session: AsyncSession,
    operator: OperatorContext,
) -> ReportService:
    settings = request.app.state.settings
    secret = (
        settings.tokenization_secret.get_secret_value()
        if settings.tokenization_secret is not None
        else None
    )
    return ReportService(
        session,
        operator=operator,
        provider=HttpLLMProvider(settings=settings),
        tokenization_secret=secret,
    )


async def _response(
    repository: ReportingRepository,
    report: GovernanceReportRecord,
) -> GovernanceReportResponse:
    job = await repository.get_job(report.job_id)
    if job is None:
        raise LookupError("report job not found")
    return GovernanceReportResponse(
        id=report.id,
        job_id=report.job_id,
        execution_id=report.execution_id,
        version=report.version,
        facts_hash=report.facts_hash,
        facts=ExecutionFactBundle.model_validate(report.facts),
        content=GovernanceReportContent.model_validate(report.content),
        html_hash=report.html_hash,
        provenance=report.provenance,
        generated_by=report.generated_by,
        generated_at=report.generated_at,
    )


async def _authorized_report(
    report_id: UUID,
    repository: ReportingRepository,
    operator: OperatorContext,
) -> tuple[GovernanceReportRecord, ReportJobRecord]:
    report = await repository.get_report(report_id)
    if report is None:
        raise LookupError("report not found")
    job = await repository.get_job(report.job_id)
    if job is None or job.tenant_id != operator.tenant_id:
        raise LookupError("report not found")
    return report, job


@router.post(
    "/execution-records/{execution_id}/reports",
    response_model=GovernanceReportResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_report(
    execution_id: UUID,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> GovernanceReportResponse:
    try:
        report = await _service(request, session, operator).generate(
            execution_id,
            idempotency_key=idempotency_key,
        )
        return await _response(ReportingRepository(session), report)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.get(
    "/execution-records/{execution_id}/reports",
    response_model=list[GovernanceReportResponse],
)
async def list_reports(
    execution_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> list[GovernanceReportResponse]:
    try:
        await ExecutionRecordService(session, operator=operator).get_detail(execution_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    repository = ReportingRepository(session)
    return [
        await _response(repository, report)
        for report in await repository.list_reports(execution_id)
    ]


@router.get("/reports/{report_id}", response_model=GovernanceReportResponse)
async def get_report(
    report_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> GovernanceReportResponse:
    repository = ReportingRepository(session)
    try:
        report, _job = await _authorized_report(report_id, repository, operator)
        return await _response(repository, report)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/reports/{report_id}/html", response_class=HTMLResponse)
async def get_report_html(
    report_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> HTMLResponse:
    repository = ReportingRepository(session)
    try:
        report, _job = await _authorized_report(report_id, repository, operator)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return HTMLResponse(
        report.html_content,
        headers={
            "Content-Disposition": f'inline; filename="governance-report-v{report.version}.html"'
        },
    )


@router.get("/reports/{report_id}/download", response_class=HTMLResponse)
async def download_report_html(
    report_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    operator: Annotated[OperatorContext, Depends(get_operator_context)],
) -> HTMLResponse:
    repository = ReportingRepository(session)
    try:
        report, _job = await _authorized_report(report_id, repository, operator)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return HTMLResponse(
        report.html_content,
        headers={
            "Content-Disposition": (
                f'attachment; filename="governance-report-v{report.version}.html"'
            )
        },
    )
