from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.repositories.differences import DifferenceRepository
from app.schemas.canonical_entities import EntityType
from app.schemas.differences import (
    DifferenceDraft,
    DifferenceEvidence,
    DifferenceType,
    FieldDifference,
)
from tests.fixtures.organization_factory import create_hierarchy_pair


@pytest.fixture
def difference_client(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'differences-api.db'}",
        upload_root=tmp_path / "uploads",
        snapshot_root=tmp_path / "snapshots",
        quarantine_root=tmp_path / "quarantine",
        auto_create_schema=True,
    )
    with TestClient(create_app(settings)) as client:
        yield client


async def seed_differences(client: TestClient) -> tuple[UUID, tuple[UUID, ...]]:
    async with client.app.state.database.session_factory() as session:
        pair = await create_hierarchy_pair(session)
        repository = DifferenceRepository(session)
        drafts = tuple(
            DifferenceDraft(
                task_id=pair.task_id,
                tenant_id="school-1",
                entity_type=entity_type,
                difference_type=difference_type,
                proposed_action=action,
                evidence=DifferenceEvidence(
                    source_snapshot_id=pair.source_snapshot_id,
                    target_snapshot_id=pair.target_snapshot_id,
                    fields=(
                        FieldDifference(
                            field="name",
                            source_value=f"source-{index}",
                            target_value=f"target-{index}",
                            normalized_source=f"source-{index}",
                            normalized_target=f"target-{index}",
                            comparison="attribute",
                        ),
                    ),
                    comparison_rule_version="comparison-v1",
                ),
            )
            for index, (entity_type, difference_type, action) in enumerate(
                (
                    (EntityType.TEACHER, DifferenceType.ATTRIBUTE_CONFLICT, "update"),
                    (EntityType.TEACHER, DifferenceType.ATTRIBUTE_CONFLICT, "update"),
                    (EntityType.STUDENT, DifferenceType.SEEWO_MISSING, "create"),
                ),
                start=1,
            )
        )
        items = await repository.insert_many(drafts)
        await session.commit()
        return pair.task_id, tuple(item.id for item in items)


def seeded(client: TestClient) -> tuple[UUID, tuple[UUID, ...]]:
    assert client.portal is not None
    return client.portal.call(seed_differences, client)


async def seed_snapshot_ready_task(client: TestClient) -> UUID:
    async with client.app.state.database.session_factory() as session:
        pair = await create_hierarchy_pair(session)
        await session.commit()
        return pair.task_id


def snapshot_ready_task(client: TestClient) -> UUID:
    assert client.portal is not None
    return client.portal.call(seed_snapshot_ready_task, client)


def test_task_can_resolve_then_detect_differences(
    difference_client: TestClient,
) -> None:
    task_id = snapshot_ready_task(difference_client)

    resolution = difference_client.post(f"/api/reconciliation-tasks/{task_id}/resolve")
    detection = difference_client.post(f"/api/reconciliation-tasks/{task_id}/differences/detect")

    assert resolution.status_code == 200
    assert resolution.json()["task_id"] == str(task_id)
    assert resolution.json()["processed_entity_types"]
    assert detection.status_code == 200


def test_resolve_unknown_task_returns_404(difference_client: TestClient) -> None:
    response = difference_client.post(f"/api/reconciliation-tasks/{uuid4()}/resolve")

    assert response.status_code == 404
    assert "reconciliation task not found" in response.json()["detail"]


def test_list_filters_and_has_stable_cursor(difference_client: TestClient) -> None:
    task_id, _ = seeded(difference_client)

    first = difference_client.get(
        f"/api/reconciliation-tasks/{task_id}/differences",
        params={
            "entity_type": "teacher",
            "difference_type": "attribute_conflict",
            "limit": 1,
        },
    )

    assert first.status_code == 200, first.text
    body = first.json()
    assert len(body["items"]) == 1
    assert body["next_cursor"]
    assert body["items"][0]["entity_type"] == "teacher"
    second = difference_client.get(
        f"/api/reconciliation-tasks/{task_id}/differences",
        params={
            "entity_type": "teacher",
            "difference_type": "attribute_conflict",
            "limit": 1,
            "cursor": body["next_cursor"],
        },
    )
    assert second.status_code == 200
    assert second.json()["items"][0]["id"] != body["items"][0]["id"]


def test_detail_returns_field_and_match_evidence(difference_client: TestClient) -> None:
    _, difference_ids = seeded(difference_client)

    response = difference_client.get(f"/api/differences/{difference_ids[0]}")

    assert response.status_code == 200
    body = response.json()
    assert body["evidence"]["fields"][0]["field"] == "name"
    assert "match_evidence" in body["evidence"]
    assert body["analysis_status"] == "pending"


def test_list_rejects_unknown_task(difference_client: TestClient) -> None:
    seeded(difference_client)

    response = difference_client.get(f"/api/reconciliation-tasks/{uuid4()}/differences")

    assert response.status_code == 404


def test_unknown_difference_returns_404(difference_client: TestClient) -> None:
    response = difference_client.get(f"/api/differences/{uuid4()}")

    assert response.status_code == 404


def test_invalid_cursor_returns_422(difference_client: TestClient) -> None:
    task_id, _ = seeded(difference_client)

    response = difference_client.get(
        f"/api/reconciliation-tasks/{task_id}/differences",
        params={"cursor": "not-a-valid-cursor!!"},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "invalid difference cursor"


def test_detect_rejects_task_before_matching(difference_client: TestClient) -> None:
    task_id, _ = seeded(difference_client)

    response = difference_client.post(f"/api/reconciliation-tasks/{task_id}/differences/detect")

    assert response.status_code == 409
    assert "matching" in response.json()["detail"]
