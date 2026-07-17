from datetime import UTC
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analyses import AnalysisRecord, ImmutableAnalysisError
from app.schemas.differences import DifferenceItem
from app.schemas.governance import (
    AnalysisProvenance,
    AnalysisResult,
    AnalysisStatus,
    CauseAnalysis,
)

DEFAULT_ANALYSIS_VERSION = "analysis-v1"

__all__ = ["AnalysisRepository", "ImmutableAnalysisError"]


class AnalysisRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_for_difference(
        self,
        difference_id: UUID,
        difference_version: int,
        analysis_version: str = DEFAULT_ANALYSIS_VERSION,
    ) -> AnalysisResult | None:
        record = await self.session.scalar(
            select(AnalysisRecord).where(
                AnalysisRecord.difference_id == difference_id,
                AnalysisRecord.difference_version == difference_version,
                AnalysisRecord.analysis_version == analysis_version,
            )
        )
        return self._result(record) if record is not None else None

    async def get_latest(self, difference_id: UUID) -> AnalysisResult | None:
        record = await self.session.scalar(
            select(AnalysisRecord)
            .where(AnalysisRecord.difference_id == difference_id)
            .order_by(AnalysisRecord.generated_at.desc(), AnalysisRecord.id.desc())
        )
        return self._result(record) if record is not None else None

    async def save_success(
        self,
        difference: DifferenceItem,
        output: CauseAnalysis,
        provenance: AnalysisProvenance,
        *,
        attempt_count: int = 1,
        analysis_version: str = DEFAULT_ANALYSIS_VERSION,
    ) -> AnalysisResult:
        return await self._save(
            difference,
            status=AnalysisStatus.SUCCEEDED,
            output=output,
            failure_code=None,
            attempt_count=attempt_count,
            provenance=provenance,
            analysis_version=analysis_version,
        )

    async def record_failure(
        self,
        difference: DifferenceItem,
        *,
        attempt_count: int,
        failure_code: str,
        provenance: AnalysisProvenance,
        analysis_version: str = DEFAULT_ANALYSIS_VERSION,
    ) -> AnalysisResult:
        return await self._save(
            difference,
            status=AnalysisStatus.FAILED,
            output=None,
            failure_code=failure_code,
            attempt_count=attempt_count,
            provenance=provenance,
            analysis_version=analysis_version,
        )

    async def save_manual_review(
        self,
        difference: DifferenceItem,
        output: CauseAnalysis,
        provenance: AnalysisProvenance,
        *,
        attempt_count: int,
        failure_code: str | None = None,
        analysis_version: str = DEFAULT_ANALYSIS_VERSION,
    ) -> AnalysisResult:
        return await self._save(
            difference,
            status=AnalysisStatus.MANUAL_REVIEW,
            output=output,
            failure_code=failure_code,
            attempt_count=attempt_count,
            provenance=provenance,
            analysis_version=analysis_version,
        )

    async def _save(
        self,
        difference: DifferenceItem,
        *,
        status: AnalysisStatus,
        output: CauseAnalysis | None,
        failure_code: str | None,
        attempt_count: int,
        provenance: AnalysisProvenance,
        analysis_version: str,
    ) -> AnalysisResult:
        existing = await self.get_for_difference(
            difference.id, difference.version, analysis_version
        )
        if existing is not None:
            return existing
        record = AnalysisRecord(
            difference_id=difference.id,
            difference_version=difference.version,
            analysis_version=analysis_version,
            status=status.value,
            output=output.model_dump(mode="json") if output is not None else None,
            failure_code=failure_code,
            attempt_count=attempt_count,
            provider=provenance.provider,
            model=provenance.model,
            skill_name=provenance.skill_name,
            skill_version=provenance.skill_version,
            prompt_version=provenance.prompt_version,
            tool_trace_ids=list(provenance.tool_trace_ids),
            usage=provenance.usage.model_dump(mode="json"),
            generated_at=provenance.generated_at,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(record)
                await self.session.flush()
        except IntegrityError:
            existing = await self.get_for_difference(
                difference.id, difference.version, analysis_version
            )
            if existing is not None:
                return existing
            raise
        return self._result(record)

    @staticmethod
    def _result(record: AnalysisRecord) -> AnalysisResult:
        output = CauseAnalysis.model_validate(record.output) if record.output is not None else None
        generated_at = record.generated_at
        if generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=UTC)
        return AnalysisResult(
            id=record.id,
            difference_id=record.difference_id,
            difference_version=record.difference_version,
            analysis_version=record.analysis_version,
            status=AnalysisStatus(record.status),
            output=output,
            failure_code=record.failure_code,
            attempt_count=record.attempt_count,
            provenance=AnalysisProvenance(
                provider=record.provider,
                model=record.model,
                skill_name=record.skill_name,
                skill_version=record.skill_version,
                prompt_version=record.prompt_version,
                tool_trace_ids=tuple(record.tool_trace_ids),
                usage=record.usage,
                generated_at=generated_at,
            ),
        )
