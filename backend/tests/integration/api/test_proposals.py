from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import Settings
from app.main import create_app
from app.models.snapshots import CanonicalEntityRecord, Snapshot
from app.repositories.differences import DifferenceRepository
from app.schemas.canonical_entities import EntityType
from app.schemas.differences import (
    DifferenceAction,
    DifferenceDraft,
    DifferenceEvidence,
    DifferenceType,
)
from tests.fixtures.organization_factory import create_hierarchy_pair


@pytest.fixture
def proposal_client(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'proposals-api.db'}",
        upload_root=tmp_path / "uploads",
        snapshot_root=tmp_path / "snapshots",
        quarantine_root=tmp_path / "quarantine",
        auto_create_schema=True,
    )
    with TestClient(create_app(settings)) as client:
        yield client


async def seed_analyzed_missing(client: TestClient) -> tuple[UUID, UUID, str]:
    async with client.app.state.database.session_factory() as session:
        pair = await create_hierarchy_pair(session)
        source = await session.scalar(
            select(CanonicalEntityRecord).where(
                CanonicalEntityRecord.snapshot_id == pair.source_snapshot_id,
                CanonicalEntityRecord.entity_type == EntityType.TEACHER.value,
            )
        )
        target_snapshot = await session.get(Snapshot, pair.target_snapshot_id)
        assert source is not None and target_snapshot is not None
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
        return pair.task_id, difference.id, target_snapshot.content_hash


def seeded(client: TestClient) -> tuple[UUID, UUID, str]:
    assert client.portal is not None
    task_id, difference_id, target_hash = client.portal.call(seed_analyzed_missing, client)
    response = client.post(f"/api/reconciliation-tasks/{task_id}/analyses")
    assert response.status_code == 202, response.text
    return task_id, difference_id, target_hash


def test_editor_schema_is_backend_owned(proposal_client: TestClient) -> None:
    response = proposal_client.get("/api/entity-editor-schemas/teacher")

    assert response.status_code == 200
    fields = {field["name"]: field for field in response.json()["fields"]}
    assert fields["phone"]["field_type"] == "phone"
    assert fields["email"]["field_type"] == "email"
    assert "snapshot_id" not in fields


def test_ai_and_manual_proposals_share_versioned_pending_contract(
    proposal_client: TestClient,
) -> None:
    _task_id, difference_id, target_hash = seeded(proposal_client)
    analysis = proposal_client.get(f"/api/differences/{difference_id}/analysis").json()
    option_id = analysis["output"]["options"][0]["option_id"]
    ai_request = {
        "analysis_id": analysis["id"],
        "option_id": option_id,
        "expected_difference_version": 1,
    }

    preview = proposal_client.post(
        f"/api/differences/{difference_id}/proposals/from-analysis/preview",
        json=ai_request,
    )
    confirmed = proposal_client.post(
        f"/api/differences/{difference_id}/proposals/from-analysis",
        json=ai_request,
    )
    manual = proposal_client.post(
        f"/api/differences/{difference_id}/proposals/manual",
        json={
            "expected_difference_version": 1,
            "operation_type": "create",
            "target_entity_id": None,
            "changes": {"phone": "13700000000"},
            "rationale": "The operator verified this phone through the school directory",
        },
    )

    assert preview.status_code == 200, preview.text
    assert confirmed.status_code == 201, confirmed.text
    assert confirmed.json()["proposal_source"] == "ai"
    assert confirmed.json()["status"] == "pending_execution"
    assert manual.status_code == 201, manual.text
    assert manual.json()["proposal_source"] == "operator"
    assert manual.json()["proposal_version"] == 2
    assert manual.json()["supersedes_id"] == confirmed.json()["id"]

    difference = proposal_client.get(f"/api/differences/{difference_id}")
    assert difference.json()["proposal_status"] == "pending_execution"
    assert difference.json()["current_proposal_version"] == 2

    history = proposal_client.get(f"/api/differences/{difference_id}/proposals")
    detail = proposal_client.get(f"/api/proposals/{manual.json()['id']}")
    assert history.status_code == 200
    assert [item["proposal_version"] for item in history.json()] == [2, 1]
    assert detail.status_code == 200

    async def current_hash() -> str:
        async with proposal_client.app.state.database.session_factory() as session:
            difference = await DifferenceRepository(session).get(difference_id)
            assert difference is not None
            snapshot = await session.get(Snapshot, difference.evidence.target_snapshot_id)
            assert snapshot is not None
            return snapshot.content_hash

    assert proposal_client.portal is not None
    assert proposal_client.portal.call(current_hash) == target_hash


def test_proposal_routes_reject_stale_and_cross_tenant_requests(
    proposal_client: TestClient,
) -> None:
    _task_id, difference_id, _target_hash = seeded(proposal_client)
    stale = proposal_client.post(
        f"/api/differences/{difference_id}/proposals/manual/preview",
        json={
            "expected_difference_version": 2,
            "operation_type": "create",
            "target_entity_id": None,
            "changes": {"phone": "13700000000"},
            "rationale": "The operator verified this phone through the school directory",
        },
    )
    assert stale.status_code == 409

    proposal_client.app.state.settings.demo_tenant_id = "other-school"
    assert proposal_client.get(f"/api/differences/{difference_id}/proposals").status_code == 404
    assert proposal_client.get(f"/api/proposals/{uuid4()}").status_code == 404
