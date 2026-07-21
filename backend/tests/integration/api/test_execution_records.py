from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from tests.integration.api.test_execution_preview import _preview


@pytest.fixture
def execution_record_client(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'execution-records.db'}",
        upload_root=tmp_path / "uploads",
        snapshot_root=tmp_path / "snapshots",
        quarantine_root=tmp_path / "quarantine",
        auto_create_schema=True,
    )
    with TestClient(create_app(settings)) as client:
        yield client


def _confirmed_batch(
    client: TestClient,
    *,
    idempotency_key: str = "execution-record-detail",
) -> dict[str, object]:
    preview = _preview(client)
    response = client.post(
        "/api/execution-batches",
        headers={"Idempotency-Key": idempotency_key},
        json={
            "plan_id": preview["plan_id"],
            "plan_version": preview["plan_version"],
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


def test_execution_history_returns_backend_actors_and_versioned_proposals(
    execution_record_client: TestClient,
) -> None:
    batch = _confirmed_batch(execution_record_client)

    detail = execution_record_client.get(f"/api/execution-records/{batch['id']}")
    history = execution_record_client.get("/api/execution-records?limit=10")

    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["status"] == "confirmed"
    assert body["confirmed_by"] == "demo-operator"
    assert body["source_snapshot_id"]
    assert body["target_snapshot_id"]
    assert body["operations"][0]["proposal_source"] == "ai"
    assert body["operations"][0]["proposal_version"] == 1
    assert body["operations"][0]["proposal_created_by"] == "demo-operator"
    assert body["operations"][0]["attempts"] == []
    assert history.status_code == 200, history.text
    assert history.json()["items"][0]["id"] == batch["id"]


def test_retry_rejects_operations_without_retryable_failure(
    execution_record_client: TestClient,
) -> None:
    batch = _confirmed_batch(execution_record_client)
    detail = execution_record_client.get(f"/api/execution-records/{batch['id']}").json()
    operation_id = detail["operations"][0]["record_id"]

    spoofed = execution_record_client.post(
        f"/api/execution-batches/{batch['id']}/retry",
        json={"operation_ids": [operation_id], "operator_id": "spoofed"},
    )
    rejected = execution_record_client.post(
        f"/api/execution-batches/{batch['id']}/retry",
        json={"operation_ids": [operation_id]},
    )

    assert spoofed.status_code == 422
    assert rejected.status_code == 409


def test_execution_history_cursor_does_not_repeat_records(
    execution_record_client: TestClient,
) -> None:
    first = _confirmed_batch(
        execution_record_client,
        idempotency_key="execution-history-first",
    )
    second = _confirmed_batch(
        execution_record_client,
        idempotency_key="execution-history-second",
    )

    first_page = execution_record_client.get("/api/execution-records?limit=1")
    assert first_page.status_code == 200, first_page.text
    cursor = first_page.json()["next_cursor"]
    assert cursor
    second_page = execution_record_client.get(
        "/api/execution-records",
        params={"limit": 1, "cursor": cursor},
    )

    assert second_page.status_code == 200, second_page.text
    ids = {
        first_page.json()["items"][0]["id"],
        second_page.json()["items"][0]["id"],
    }
    assert ids == {first["id"], second["id"]}
