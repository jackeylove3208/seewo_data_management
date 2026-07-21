from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.differences.service import DifferenceDetectionService
from app.matching.service import EntityResolutionService
from app.models.analyses import AnalysisRecord, ImmutableAnalysisError
from app.repositories.analyses import AnalysisRepository
from app.repositories.differences import DifferenceRepository
from app.schemas.differences import DifferenceFilters
from app.schemas.governance import (
    AnalysisProvenance,
    AnalysisResult,
    AnalysisStatus,
    CauseAnalysis,
    CauseAnalysisV2,
    CauseAnalysisV3,
    GovernanceOption,
    ManualResolution,
    ManualStep,
    ProposedFieldChange,
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


def analysis_v2(target_entity_id) -> CauseAnalysisV2:
    return CauseAnalysisV2(
        cause="The authoritative value differs from the Seewo value",
        evidence_summary="The normalized phone fields are not equivalent",
        manual_only=False,
        options=(
            GovernanceOption(
                option_id="option-1",
                operation_type=RecommendedAction.UPDATE,
                target_entity_id=target_entity_id,
                proposed_changes=(
                    ProposedFieldChange(
                        field="phone",
                        before="13900000000",
                        after="13800000000",
                    ),
                ),
                rationale="Use the authoritative phone value",
                evidence_refs=("field:phone",),
                risk=RiskLevel.HIGH,
                confidence=0.9,
                recommended=True,
            ),
        ),
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


def test_analysis_rejects_whitespace_only_explanations() -> None:
    with pytest.raises(ValidationError):
        CauseAnalysis(
            cause="   ",
            evidence_summary="   ",
            recommended_action=RecommendedAction.UPDATE,
            risk=RiskLevel.LOW,
            confidence=0.8,
        )


def test_succeeded_result_requires_output() -> None:
    with pytest.raises(ValidationError):
        AnalysisResult(
            id=uuid4(),
            difference_id=uuid4(),
            difference_version=1,
            analysis_version="analysis-v1",
            status=AnalysisStatus.SUCCEEDED,
            output=None,
            attempt_count=1,
            provenance=provenance(),
        )


def test_succeeded_result_rejects_manual_review_action() -> None:
    with pytest.raises(ValidationError):
        AnalysisResult(
            id=uuid4(),
            difference_id=uuid4(),
            difference_version=1,
            analysis_version="analysis-v1",
            status=AnalysisStatus.SUCCEEDED,
            output=analysis().model_copy(
                update={"recommended_action": RecommendedAction.MANUAL_REVIEW}
            ),
            attempt_count=1,
            provenance=provenance(),
        )


def test_manual_review_result_requires_manual_review_output() -> None:
    with pytest.raises(ValidationError):
        AnalysisResult(
            id=uuid4(),
            difference_id=uuid4(),
            difference_version=1,
            analysis_version="analysis-v1",
            status=AnalysisStatus.MANUAL_REVIEW,
            output=analysis(),
            attempt_count=2,
            provenance=provenance(),
        )


def test_failed_result_rejects_successful_output() -> None:
    with pytest.raises(ValidationError):
        AnalysisResult(
            id=uuid4(),
            difference_id=uuid4(),
            difference_version=1,
            analysis_version="analysis-v1",
            status=AnalysisStatus.FAILED,
            output=analysis(),
            failure_code="invalid_model_output",
            attempt_count=2,
            provenance=provenance(),
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
async def test_manual_review_history_preserves_safe_output(
    session,
    persisted_difference,
) -> None:
    output = analysis().model_copy(
        update={
            "recommended_action": RecommendedAction.MANUAL_REVIEW,
            "risk": RiskLevel.HIGH,
            "confidence": 0,
        }
    )

    result = await AnalysisRepository(session).save_manual_review(
        persisted_difference,
        output,
        provenance(),
        attempt_count=2,
        failure_code="analysis_policy_error",
    )

    assert result.status is AnalysisStatus.MANUAL_REVIEW
    assert result.output == output
    assert result.failure_code == "analysis_policy_error"
    assert result.attempt_count == 2


@pytest.mark.asyncio
async def test_repository_returns_none_for_unknown_difference_version(session) -> None:
    assert (
        await AnalysisRepository(session).get_for_difference(
            uuid4(),
            difference_version=1,
        )
        is None
    )


@pytest.mark.asyncio
async def test_repository_round_trips_analysis_v3(
    session,
    persisted_difference,
) -> None:
    output = CauseAnalysisV3(
        locale="zh-CN",
        issue_title="需要人工核对",
        cause_summary="当前证据不足，无法安全修改。",
        evidence_summary="双方记录缺少相同的稳定身份标识。",
        business_impact="直接修改可能影响错误账号。",
        recommended_solution_id="manual-1",
        solutions=(
            ManualResolution(
                solution_id="manual-1",
                title="人工核对身份",
                rationale="先确认身份，再生成修改方案。",
                risk=RiskLevel.HIGH,
                risk_reason="身份不确定时不能自动修改。",
                confidence=0.2,
                recommended=True,
                manual_steps=(ManualStep(order=1, instruction="向学校管理员核对教师工号。"),),
            ),
        ),
    )

    saved = await AnalysisRepository(session).save_manual_review(
        persisted_difference,
        output,
        provenance(),
        attempt_count=2,
        analysis_version="analysis-v3",
    )
    loaded = await AnalysisRepository(session).get_for_difference(
        persisted_difference.id,
        persisted_difference.version,
        "analysis-v3",
    )

    assert saved == loaded
    assert isinstance(loaded.output, CauseAnalysisV3)


@pytest.mark.asyncio
async def test_difference_page_does_not_duplicate_multiple_analysis_versions(
    session,
    persisted_difference,
) -> None:
    repository = AnalysisRepository(session)
    await repository.save_success(
        persisted_difference,
        analysis_v2(persisted_difference.evidence.target_entity_id),
        provenance(),
        analysis_version="analysis-v2",
    )
    await repository.save_success(
        persisted_difference,
        analysis(),
        provenance(),
    )

    page = await DifferenceRepository(session).list_page(
        persisted_difference.task_id,
        DifferenceFilters(),
    )

    matching = [item for item in page.items if item.id == persisted_difference.id]
    assert len(matching) == 1
    assert matching[0].risk == "high"
