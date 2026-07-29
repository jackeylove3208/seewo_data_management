from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import Settings
from app.main import create_app
from app.models.snapshots import CanonicalEntityRecord
from app.repositories.analyses import AnalysisRepository
from app.repositories.differences import DifferenceRepository
from app.schemas.canonical_entities import EntityType
from app.schemas.differences import (
    DifferenceAction,
    DifferenceDraft,
    DifferenceEvidence,
    DifferenceType,
)
from app.schemas.governance import CauseAnalysis, RecommendedAction, RiskLevel
from tests.fixtures.organization_factory import create_hierarchy_pair


@pytest.fixture
def analysis_client(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'analyses-api.db'}",
        upload_root=tmp_path / "uploads",
        snapshot_root=tmp_path / "snapshots",
        quarantine_root=tmp_path / "quarantine",
        auto_create_schema=True,
    )
    with TestClient(create_app(settings)) as client:
        yield client


async def seed_missing_difference(client: TestClient) -> tuple[UUID, UUID]:
    async with client.app.state.database.session_factory() as session:
        pair = await create_hierarchy_pair(session)
        source = await session.scalar(
            select(CanonicalEntityRecord).where(
                CanonicalEntityRecord.snapshot_id == pair.source_snapshot_id,
                CanonicalEntityRecord.entity_type == EntityType.TEACHER.value,
            )
        )
        assert source is not None
        difference = (
            await DifferenceRepository(session).insert_many(
                (
                    DifferenceDraft(
                        task_id=pair.task_id,
                        tenant_id=pair.tenant_id,
                        entity_type=EntityType.TEACHER,
                        difference_type=DifferenceType.SEEWO_MISSING,
                        proposed_action=DifferenceAction.CREATE,
                        evidence=DifferenceEvidence(
                            source_snapshot_id=pair.source_snapshot_id,
                            target_snapshot_id=pair.target_snapshot_id,
                            source_entity_id=source.id,
                            source_payload=source.canonical_payload,
                            comparison_rule_version="comparison-v1",
                        ),
                    ),
                )
            )
        )[0]
        await session.commit()
        return pair.task_id, difference.id


def seeded(client: TestClient) -> tuple[UUID, UUID]:
    assert client.portal is not None
    return client.portal.call(seed_missing_difference, client)


def test_analysis_trigger_exposes_output_and_execution_eligibility(
    analysis_client: TestClient,
) -> None:
    task_id, difference_id = seeded(analysis_client)
    pending = analysis_client.get(f"/api/differences/{difference_id}")
    assert pending.status_code == 200
    assert pending.json()["analysis_status"] == "pending"
    assert pending.json()["execution_eligible"] is False

    triggered = analysis_client.post(f"/api/reconciliation-tasks/{task_id}/analyses")
    assert triggered.status_code == 202, triggered.text
    assert triggered.json()["total"] == 1
    assert triggered.json()["succeeded"] == 1

    fetched = analysis_client.get(f"/api/differences/{difference_id}/analysis")
    assert fetched.status_code == 200
    output = fetched.json()["output"]
    assert output["manual_only"] is False
    assert output["options"][0]["operation_type"] == "create"
    assert fetched.json()["provenance"]["provider"] == "deterministic"

    detail = analysis_client.get(f"/api/differences/{difference_id}").json()
    assert detail["analysis_status"] == "succeeded"
    assert detail["risk"] == "medium"
    assert detail["execution_eligible"] is True

    filtered = analysis_client.get(
        f"/api/reconciliation-tasks/{task_id}/differences",
        params={"analysis_status": "succeeded", "risk": "medium"},
    )
    assert [item["id"] for item in filtered.json()["items"]] == [str(difference_id)]
def test_unknown_analysis_returns_404(analysis_client: TestClient) -> None:
    _task_id, difference_id = seeded(analysis_client)
    response = analysis_client.get(f"/api/differences/{difference_id}/analysis")
    assert response.status_code == 404


def test_analysis_query_returns_current_supported_analysis_version(
    analysis_client: TestClient,
) -> None:
    task_id, difference_id = seeded(analysis_client)
    assert analysis_client.post(f"/api/reconciliation-tasks/{task_id}/analyses").status_code == 202

    async def add_legacy_analysis_version() -> None:
        async with analysis_client.app.state.database.session_factory() as session:
            difference = await DifferenceRepository(session).get(difference_id)
            current = await AnalysisRepository(session).get_for_difference(
                difference_id,
                1,
                "analysis-v2",
            )
            assert difference is not None and current is not None and current.output is not None
            await AnalysisRepository(session).save_success(
                difference,
                CauseAnalysis(
                    cause="Legacy deterministic analysis",
                    evidence_summary="Legacy analysis remains available by explicit version",
                    recommended_action=RecommendedAction.CREATE,
                    risk=RiskLevel.MEDIUM,
                    confidence=1,
                ),
                current.provenance,
                analysis_version="analysis-v1",
            )
            await session.commit()

    assert analysis_client.portal is not None
    analysis_client.portal.call(add_legacy_analysis_version)

    response = analysis_client.get(f"/api/differences/{difference_id}/analysis")
    assert response.status_code == 200
    assert response.json()["analysis_version"] == "analysis-v2"


def test_analysis_and_difference_endpoints_reject_cross_tenant_access(
    analysis_client: TestClient,
) -> None:
    task_id, difference_id = seeded(analysis_client)
    assert analysis_client.post(f"/api/reconciliation-tasks/{task_id}/analyses").status_code == 202
    analysis_client.app.state.settings.demo_tenant_id = "other-school"

    assert analysis_client.post(f"/api/reconciliation-tasks/{task_id}/analyses").status_code == 404
    assert analysis_client.get(f"/api/differences/{difference_id}/analysis").status_code == 404
    assert analysis_client.get(f"/api/differences/{difference_id}").status_code == 404
    assert (
        analysis_client.get(f"/api/reconciliation-tasks/{task_id}/differences").status_code == 404
    )
