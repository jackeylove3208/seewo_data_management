from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_operator_context
from app.core.config import Settings
from app.core.security import OperatorContext
from app.main import create_app
from app.schemas.differences import DifferenceType
from tests.integration.ai.test_analysis_service import seed_difference


@pytest.fixture
def job_client(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'analysis-jobs-api.db'}",
        upload_root=tmp_path / "uploads",
        snapshot_root=tmp_path / "snapshots",
        quarantine_root=tmp_path / "quarantine",
        auto_create_schema=True,
    )
    with TestClient(create_app(settings)) as client:
        yield client


async def seed_task(client: TestClient) -> UUID:
    async with client.app.state.database.session_factory() as session:
        difference = await seed_difference(session, DifferenceType.ATTRIBUTE_CONFLICT)
        await session.commit()
        return difference.task_id


def seeded_task(client: TestClient) -> UUID:
    assert client.portal is not None
    return client.portal.call(seed_task, client)


def test_create_and_get_analysis_job_are_idempotent(job_client: TestClient) -> None:
    task_id = seeded_task(job_client)
    first = job_client.post(
        f"/api/reconciliation-tasks/{task_id}/analysis-jobs",
        headers={"Idempotency-Key": "analysis-api-1"},
    )
    second = job_client.post(
        f"/api/reconciliation-tasks/{task_id}/analysis-jobs",
        headers={"Idempotency-Key": "analysis-api-1"},
    )

    assert first.status_code == 202, first.text
    assert second.status_code == 202
    assert first.json()["job_id"] == second.json()["job_id"]
    assert first.json()["total"] == 1
    fetched = job_client.get(f"/api/analysis-jobs/{first.json()['job_id']}")
    assert fetched.status_code == 200
    assert fetched.json()["completed"] == 0


def test_analysis_job_endpoints_hide_cross_tenant_jobs(job_client: TestClient) -> None:
    task_id = seeded_task(job_client)
    created = job_client.post(
        f"/api/reconciliation-tasks/{task_id}/analysis-jobs",
        headers={"Idempotency-Key": "analysis-api-tenant"},
    )
    job_id = created.json()["job_id"]
    job_client.app.dependency_overrides[get_operator_context] = lambda: OperatorContext(
        operator_id="other-operator",
        tenant_id="other-school",
    )
    try:
        assert job_client.get(f"/api/analysis-jobs/{job_id}").status_code == 404
        assert job_client.post(f"/api/analysis-jobs/{job_id}/retry").status_code == 404
        assert job_client.post(f"/api/analysis-jobs/{job_id}/cancel").status_code == 404
        assert job_client.get(f"/api/analysis-jobs/{job_id}/events").status_code == 404
    finally:
        job_client.app.dependency_overrides.pop(get_operator_context, None)


def test_canceled_job_emits_terminal_sse_snapshot(job_client: TestClient) -> None:
    task_id = seeded_task(job_client)
    created = job_client.post(
        f"/api/reconciliation-tasks/{task_id}/analysis-jobs",
        headers={"Idempotency-Key": "analysis-api-events"},
    )
    job_id = created.json()["job_id"]

    canceled = job_client.post(f"/api/analysis-jobs/{job_id}/cancel")
    events = job_client.get(f"/api/analysis-jobs/{job_id}/events")

    assert canceled.status_code == 200
    assert canceled.json()["status"] == "canceled"
    assert events.status_code == 200
    assert events.headers["content-type"].startswith("text/event-stream")
    assert "event: progress" in events.text
    assert '"status":"canceled"' in events.text
