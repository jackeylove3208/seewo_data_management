from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.ai.agent import AgentResult
from app.ai.analysis_service import AnalysisService
from app.ai.providers.base import ModelUsage
from app.governance.eligibility import ExecutionEligibility
from app.models.snapshots import CanonicalEntityRecord
from app.repositories.differences import DifferenceRepository
from app.schemas.canonical_entities import EntityType
from app.schemas.differences import (
    DifferenceAction,
    DifferenceDraft,
    DifferenceEvidence,
    DifferenceType,
    FieldDifference,
)
from app.schemas.governance import (
    AnalysisProvenance,
    AnalysisStatus,
    CauseAnalysis,
    RecommendedAction,
    RiskLevel,
)
from tests.fixtures.organization_factory import create_hierarchy_pair


class AgentSpy:
    def __init__(self, action: RecommendedAction = RecommendedAction.UPDATE) -> None:
        self.action = action
        self.calls = 0

    async def analyze(self, _request):
        self.calls += 1
        return AgentResult(
            output=CauseAnalysis(
                cause="The governed attributes differ",
                evidence_summary="The persisted field evidence shows different normalized values",
                recommended_action=self.action,
                risk=RiskLevel.LOW,
                confidence=0.9,
            ),
            provenance=AnalysisProvenance(
                provider="agent-provider",
                model="agent-model",
                skill_name="analyze-data-difference",
                skill_version="1.0.0",
                prompt_version="analysis-prompt-v1",
                usage=ModelUsage(input_tokens=5, output_tokens=3),
                generated_at=datetime.now(UTC),
            ),
        )


async def seed_difference(session, difference_type: DifferenceType):
    pair = await create_hierarchy_pair(session)
    source = await session.scalar(
        select(CanonicalEntityRecord).where(
            CanonicalEntityRecord.snapshot_id == pair.source_snapshot_id,
            CanonicalEntityRecord.entity_type == EntityType.TEACHER.value,
        )
    )
    target = await session.scalar(
        select(CanonicalEntityRecord).where(
            CanonicalEntityRecord.snapshot_id == pair.target_snapshot_id,
            CanonicalEntityRecord.entity_type == EntityType.TEACHER.value,
        )
    )
    assert source is not None and target is not None
    is_missing = difference_type is DifferenceType.SEEWO_MISSING
    draft = DifferenceDraft(
        task_id=pair.task_id,
        tenant_id=pair.tenant_id,
        entity_type=EntityType.TEACHER,
        difference_type=difference_type,
        proposed_action=(DifferenceAction.CREATE if is_missing else DifferenceAction.UPDATE),
        evidence=DifferenceEvidence(
            source_snapshot_id=pair.source_snapshot_id,
            target_snapshot_id=pair.target_snapshot_id,
            source_entity_id=source.id,
            target_entity_id=None if is_missing else target.id,
            fields=(
                ()
                if is_missing
                else (
                    FieldDifference(
                        field="phone",
                        source_value="13800000000",
                        target_value="13900000000",
                        normalized_source="13800000000",
                        normalized_target="13900000000",
                        comparison="attribute",
                    ),
                )
            ),
            source_payload=source.canonical_payload,
            target_payload=None if is_missing else target.canonical_payload,
            comparison_rule_version="comparison-v1",
        ),
    )
    return (await DifferenceRepository(session).insert_many((draft,)))[0]


@pytest.mark.asyncio
async def test_clear_missing_case_uses_deterministic_analysis_without_agent(session) -> None:
    difference = await seed_difference(session, DifferenceType.SEEWO_MISSING)
    agent = AgentSpy()

    result = await AnalysisService(session, agent=agent).analyze(difference.id)

    assert result.status is AnalysisStatus.SUCCEEDED
    assert result.output is not None
    assert result.output.cause == "Authoritative entity has no accepted Seewo mapping"
    assert result.output.recommended_action is RecommendedAction.CREATE
    assert result.provenance.provider == "deterministic"
    assert agent.calls == 0


@pytest.mark.asyncio
async def test_ambiguous_attribute_case_uses_agent_once_and_is_cached(session) -> None:
    difference = await seed_difference(session, DifferenceType.ATTRIBUTE_CONFLICT)
    agent = AgentSpy()
    service = AnalysisService(session, agent=agent)

    first = await service.analyze(difference.id)
    second = await service.analyze(difference.id)

    assert first.id == second.id
    assert first.status is AnalysisStatus.SUCCEEDED
    assert agent.calls == 1


@pytest.mark.asyncio
async def test_invalid_action_twice_routes_to_manual_review(session) -> None:
    difference = await seed_difference(session, DifferenceType.ATTRIBUTE_CONFLICT)
    agent = AgentSpy(action=RecommendedAction.DISABLE)

    result = await AnalysisService(session, agent=agent).analyze(difference.id)

    assert result.status is AnalysisStatus.MANUAL_REVIEW
    assert result.output is not None
    assert result.output.recommended_action is RecommendedAction.MANUAL_REVIEW
    assert result.output.risk is RiskLevel.HIGH
    assert result.output.confidence == 0
    assert result.attempt_count == 2
    assert result.failure_code == "analysis_policy_error"
    assert result.provenance.provider == "agent-provider"
    assert result.provenance.model == "agent-model"
    assert result.provenance.usage.input_tokens == 10
    assert result.provenance.usage.output_tokens == 6
    assert agent.calls == 2


@pytest.mark.asyncio
async def test_valid_manual_review_recommendation_is_not_executable(session) -> None:
    difference = await seed_difference(session, DifferenceType.ATTRIBUTE_CONFLICT)
    agent = AgentSpy(action=RecommendedAction.MANUAL_REVIEW)

    result = await AnalysisService(session, agent=agent).analyze(difference.id)

    assert result.status is AnalysisStatus.MANUAL_REVIEW
    assert result.output is not None
    assert result.output.recommended_action is RecommendedAction.MANUAL_REVIEW
    assert not await ExecutionEligibility(session).is_eligible(difference.id, difference.version)
