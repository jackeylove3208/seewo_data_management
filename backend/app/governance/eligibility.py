from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.analyses import (
    ANALYSIS_V3_VERSION,
    CURRENT_ANALYSIS_VERSION,
    AnalysisRepository,
)
from app.repositories.differences import DifferenceRepository
from app.schemas.governance import (
    AnalysisResult,
    AnalysisStatus,
    AutoExecutableResolution,
    CauseAnalysisV2,
    CauseAnalysisV3,
)


class ExecutionIneligible(ValueError):
    pass


class ExecutionEligibility:
    def __init__(self, session: AsyncSession) -> None:
        self.differences = DifferenceRepository(session)
        self.analyses = AnalysisRepository(session)

    async def require_analyzed(self, difference_id: UUID, version: int) -> AnalysisResult:
        difference = await self.differences.get(difference_id)
        if difference is None:
            raise ExecutionIneligible("difference does not exist")
        if difference.version != version:
            raise ExecutionIneligible("analysis must match the current difference version")
        result = await self.analyses.get_for_difference(
            difference.id,
            difference.version,
            ANALYSIS_V3_VERSION,
        )
        if result is None:
            result = await self.analyses.get_for_difference(
                difference.id,
                difference.version,
                CURRENT_ANALYSIS_VERSION,
            )
        v2_eligible = (
            isinstance(result.output, CauseAnalysisV2) and not result.output.manual_only
            if result is not None
            else False
        )
        v3_eligible = (
            isinstance(result.output, CauseAnalysisV3)
            and any(
                isinstance(solution, AutoExecutableResolution)
                for solution in result.output.solutions
            )
            if result is not None
            else False
        )
        if (
            result is None
            or result.status is not AnalysisStatus.SUCCEEDED
            or result.output is None
            or not (v2_eligible or v3_eligible)
        ):
            raise ExecutionIneligible("valid analysis for current difference version is required")
        return result

    async def is_eligible(self, difference_id: UUID, version: int) -> bool:
        try:
            await self.require_analyzed(difference_id, version)
        except ExecutionIneligible:
            return False
        return True
