from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.analyses import AnalysisRepository
from app.repositories.differences import DifferenceRepository
from app.schemas.governance import (
    AnalysisResult,
    AnalysisStatus,
    RecommendedAction,
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
        result = await self.analyses.get_for_difference(difference.id, difference.version)
        if (
            result is None
            or result.status is not AnalysisStatus.SUCCEEDED
            or result.output is None
            or result.output.recommended_action is RecommendedAction.MANUAL_REVIEW
        ):
            raise ExecutionIneligible("valid analysis for current difference version is required")
        return result

    async def is_eligible(self, difference_id: UUID, version: int) -> bool:
        try:
            await self.require_analyzed(difference_id, version)
        except ExecutionIneligible:
            return False
        return True
