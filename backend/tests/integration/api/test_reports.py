from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.repositories.executions import ExecutionRepository
from tests.integration.api.test_execution_records import _confirmed_batch


@pytest.fixture
def report_client(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'reports.db'}",
        upload_root=tmp_path / "uploads",
        snapshot_root=tmp_path / "snapshots",
        quarantine_root=tmp_path / "quarantine",
        export_root=tmp_path / "exports",
        auto_create_schema=True,
    )
    with TestClient(create_app(settings)) as client:
        yield client


def _executed_batch(client: TestClient) -> dict[str, object]:
    batch = _confirmed_batch(client, idempotency_key="report-execution")

    async def mark_succeeded():
        async with client.app.state.database.session_factory() as session:
            repository = ExecutionRepository(session)
            stored = await repository.get_batch(UUID(str(batch["id"])))
            root = await repository.get_target_version(stored.input_target_version_id)
            plan = await repository.get_plan(stored.plan_id)
            output = await repository.create_target_version(
                task_id=plan.task_id,
                tenant_id="school-1",
                source_snapshot_id=plan.target_snapshot_id,
                parent_version_id=root.id,
                batch_id=stored.id,
                file_sha256="a" * 64,
                content_hash="b" * 64,
                storage_path=f"/tmp/report-output-{uuid4()}.csv",
            )
            operation = (await repository.list_operations(stored.id))[0]
            await repository.append_attempt(
                operation.id,
                status="succeeded",
                actual_after=operation.after,
                verification={"valid": True},
                target_version_id=output.id,
            )
            await session.commit()

    client.portal.call(mark_succeeded)
    return batch


def test_generate_list_and_download_html_report(report_client: TestClient) -> None:
    batch = _executed_batch(report_client)

    created = report_client.post(
        f"/api/execution-records/{batch['id']}/reports",
        headers={"Idempotency-Key": "report-api-1"},
    )
    repeated = report_client.post(
        f"/api/execution-records/{batch['id']}/reports",
        headers={"Idempotency-Key": "report-api-1"},
    )

    assert created.status_code == 202, created.text
    assert repeated.json()["id"] == created.json()["id"]
    assert created.json()["generated_by"] == "demo-operator"
    assert created.json()["facts"]["input_target_version_id"]
    assert created.json()["facts"]["output_target_version_ids"]
    reports = report_client.get(f"/api/execution-records/{batch['id']}/reports")
    assert reports.status_code == 200
    assert reports.json()[0]["version"] == 1
    html = report_client.get(f"/api/reports/{created.json()['id']}/html")
    assert html.status_code == 200
    assert html.headers["content-type"].startswith("text/html")
    assert "组织数据治理报告" in html.text
    download = report_client.get(f"/api/reports/{created.json()['id']}/download")
    assert download.status_code == 200
    assert download.headers["content-disposition"].startswith("attachment;")


def test_confirmed_execution_report_is_rejected(report_client: TestClient) -> None:
    batch = _confirmed_batch(report_client, idempotency_key="unreportable")

    response = report_client.post(
        f"/api/execution-records/{batch['id']}/reports",
        headers={"Idempotency-Key": "report-confirmed"},
    )

    assert response.status_code == 409
