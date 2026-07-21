from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_operator_context
from app.core.config import Settings
from app.core.security import OperatorContext
from app.main import create_app
from tests.integration.governance.test_batch_service import analyzed_terminal_job


@pytest.fixture
def batch_client(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'batch-api.db'}",
        upload_root=tmp_path / "uploads",
        snapshot_root=tmp_path / "snapshots",
        quarantine_root=tmp_path / "quarantine",
        auto_create_schema=True,
    )
    with TestClient(create_app(settings)) as client:
        yield client


async def seed_batch(client: TestClient):
    async with client.app.state.database.session_factory() as session:
        difference, analysis, job = await analyzed_terminal_job(session)
        await session.commit()
        return difference.task_id, difference.id, analysis.id, job.id


def seeded_batch(client: TestClient):
    assert client.portal is not None
    return client.portal.call(seed_batch, client)


def test_batch_summary_preview_and_confirmation_api(batch_client: TestClient) -> None:
    task_id, difference_id, _analysis_id, job_id = seeded_batch(batch_client)

    summary = batch_client.get(f"/api/reconciliation-tasks/{task_id}/analysis-summary")
    preview = batch_client.post(
        f"/api/reconciliation-tasks/{task_id}/proposal-batches/preview",
        json={"analysis_job_id": str(job_id)},
    )
    confirmed = batch_client.post(
        f"/api/reconciliation-tasks/{task_id}/proposal-batches/confirm",
        json={
            "preview_token": preview.json()["preview_token"],
            "idempotency_key": "batch-api-confirm-1",
        },
    )

    assert summary.status_code == 200
    assert summary.json()["terminal"] is True
    assert summary.json()["entity_types"][0]["proposal_ready"] == 1
    assert preview.status_code == 200, preview.text
    assert preview.json()["included"][0]["difference_id"] == str(difference_id)
    assert confirmed.status_code == 201, confirmed.text
    assert confirmed.json()["created"] == 1


def test_batch_api_hides_cross_tenant_task(batch_client: TestClient) -> None:
    task_id, _difference_id, _analysis_id, job_id = seeded_batch(batch_client)
    batch_client.app.dependency_overrides[get_operator_context] = lambda: OperatorContext(
        operator_id="other-operator",
        tenant_id="other-school",
    )
    try:
        assert (
            batch_client.get(f"/api/reconciliation-tasks/{task_id}/analysis-summary").status_code
            == 404
        )
        assert (
            batch_client.post(
                f"/api/reconciliation-tasks/{task_id}/proposal-batches/preview",
                json={"analysis_job_id": str(job_id)},
            ).status_code
            == 404
        )
    finally:
        batch_client.app.dependency_overrides.pop(get_operator_context, None)
