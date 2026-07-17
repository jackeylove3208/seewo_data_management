from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.differences.service import DifferenceDetectionService
from app.matching.service import EntityResolutionService
from app.models.analyses import AnalysisRecord, ImmutableAnalysisError
from app.repositories.analyses import AnalysisRepository
from app.repositories.differences import DifferenceRepository
from app.schemas.governance import (
    AnalysisProvenance,
    AnalysisStatus,
    CauseAnalysis,
    RecommendedAction,
    RiskLevel,
)
from tests.fixtures.organization_factory import create_hierarchy_pair


def analysis() -> CauseAnalysis:
    return CauseAnalysis(
        cause="The authoritative value differs from the Seewo value",
        evidence_summary="The normalized phone fields are not equivalent",
        recommended_action=RecommendedAction.UPDATE,
        risk=RiskLevel.LOW,
        confidence=0.9,
    )


def provenance() -> AnalysisProvenance:
    return AnalysisProvenance(
        provider="test-provider",
        model="test-model",
        skill_name="analyze-data-difference",
        skill_version="1.0.0",
        prompt_version="analysis-prompt-v1",
        tool_trace_ids=("trace-1",),
        generated_at=datetime.now(UTC),
    )


@pytest.fixture
async def persisted_difference(session):
    pair = await create_hierarchy_pair(session)
    await EntityResolutionService(session).resolve(pair)
    await DifferenceDetectionService(session).detect(pair.task_id)
    difference = (await DifferenceRepository(session).for_task(pair.task_id))[0]
    return difference


def test_analysis_rejects_confidence_outside_range() -> None:
    with pytest.raises(ValidationError):
        CauseAnalysis(
            cause="A valid cause",
            evidence_summary="A valid evidence summary",
            recommended_action=RecommendedAction.UPDATE,
            risk=RiskLevel.LOW,
            confidence=1.2,
        )


@pytest.mark.asyncio
async def test_successful_analysis_is_version_bound_and_idempotent(
    session,
    persisted_difference,
) -> None:
    repository = AnalysisRepository(session)

    first = await repository.save_success(persisted_difference, analysis(), provenance())
    second = await repository.save_success(persisted_difference, analysis(), provenance())

    assert first.id == second.id
    assert first.difference_id == persisted_difference.id
    assert first.difference_version == persisted_difference.version
    assert first.status is AnalysisStatus.SUCCEEDED
    assert first.output == analysis()
    assert first.provenance.skill_version == "1.0.0"


@pytest.mark.asyncio
async def test_analysis_history_is_immutable(session, persisted_difference) -> None:
    repository = AnalysisRepository(session)
    saved = await repository.save_success(persisted_difference, analysis(), provenance())
    record = await session.get(AnalysisRecord, saved.id)
    assert record is not None

    record.failure_code = "changed"
    with pytest.raises(ImmutableAnalysisError):
        await session.flush()


@pytest.mark.asyncio
async def test_failure_history_preserves_attempt_count(session, persisted_difference) -> None:
    result = await AnalysisRepository(session).record_failure(
        persisted_difference,
        attempt_count=1,
        failure_code="invalid_model_output",
        provenance=provenance(),
    )

    assert result.status is AnalysisStatus.FAILED
    assert result.output is None
    assert result.failure_code == "invalid_model_output"
    assert result.attempt_count == 1


@pytest.mark.asyncio
async def test_repository_returns_none_for_unknown_difference_version(session) -> None:
    assert (
        await AnalysisRepository(session).get_for_difference(
            uuid4(),
            difference_version=1,
        )
        is None
    )
