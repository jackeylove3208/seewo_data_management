from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_session
from app.ingestion.field_mapping import default_mapping_registry
from app.ingestion.service import IngestionServiceError, UploadService
from app.schemas.api_ingestion import (
    FieldMappingPreviewRequest,
    FieldMappingPreviewResponse,
    FieldMappingSummary,
    UploadResponse,
)
from app.schemas.canonical_entities import SourceRole

router = APIRouter(prefix="/api", tags=["ingestion"])


@router.post("/uploads", status_code=status.HTTP_201_CREATED, response_model=UploadResponse)
async def upload_csv(
    request: Request,
    file: Annotated[UploadFile, File()],
    source_role: Annotated[SourceRole, Form()],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UploadResponse:
    try:
        return await UploadService(session, request.app.state.settings).store(file, source_role)
    except IngestionServiceError as error:
        raise HTTPException(error.status_code, detail=error.as_detail()) from error


@router.get("/field-mappings", response_model=tuple[FieldMappingSummary, ...])
async def list_field_mappings() -> tuple[FieldMappingSummary, ...]:
    return tuple(
        FieldMappingSummary(
            version=profile.version,
            name=profile.name,
            source_role=profile.source_role,
        )
        for profile in default_mapping_registry().list()
    )


@router.post(
    "/uploads/{upload_id}/mapping-preview",
    response_model=FieldMappingPreviewResponse,
)
async def preview_mapping(
    upload_id: UUID,
    body: FieldMappingPreviewRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FieldMappingPreviewResponse:
    try:
        return await UploadService(session, request.app.state.settings).preview(
            upload_id,
            body.mapping_version,
            default_mapping_registry(),
        )
    except IngestionServiceError as error:
        raise HTTPException(error.status_code, detail=error.as_detail()) from error
