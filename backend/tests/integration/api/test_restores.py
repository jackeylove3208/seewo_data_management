import asyncio
import hashlib
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from app.core.config import Settings
from app.main import create_app
from app.models.executions import TargetVersionRecord
from app.repositories.executions import ExecutionRepository
from tests.integration.api.test_reports import _executed_batch


@pytest.fixture
def restore_client(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'restores.db'}",
        upload_root=tmp_path / "uploads",
        snapshot_root=tmp_path / "snapshots",
        quarantine_root=tmp_path / "quarantine",
        export_root=tmp_path / "exports",
        auto_create_schema=True,
    )
    with TestClient(create_app(settings)) as client:
        yield client


def test_preview_and_confirm_historical_restore(restore_client: TestClient) -> None:
    batch = _executed_batch(restore_client)
    detail = restore_client.get(f"/api/execution-records/{batch['id']}").json()
    task_id = detail["task_id"]
    versions = restore_client.get(f"/api/reconciliation-tasks/{task_id}/target-versions")
    assert versions.status_code == 200
    assert len(versions.json()) == 2
    root = next(item for item in versions.json() if item["parent_version_id"] is None)

    async def materialize_versions():
        async with restore_client.app.state.database.session_factory() as session:
            repository = ExecutionRepository(session)
            root_record = await repository.get_target_version(UUID(root["id"]))
            output_record = await repository.get_target_version(
                UUID(detail["output_target_version_ids"][-1])
            )
            await asyncio.to_thread(
                Path(root_record.storage_path).write_text,
                "entity_type,id,name\n",
                encoding="utf-8",
            )
            await asyncio.to_thread(
                Path(output_record.storage_path).write_text,
                "entity_type,id,name,parent_id\n教师,t-a,张三,d-a\n",
                encoding="utf-8",
            )
            for record in (root_record, output_record):
                content = await asyncio.to_thread(Path(record.storage_path).read_bytes)
                file_hash = hashlib.sha256(content).hexdigest()
                await session.execute(
                    update(TargetVersionRecord)
                    .where(TargetVersionRecord.id == record.id)
                    .values(file_sha256=file_hash)
                )
            await session.commit()

    restore_client.portal.call(materialize_versions)

    preview = restore_client.post(f"/api/target-versions/{root['id']}/restore-preview")
    assert preview.status_code == 200, preview.text
    assert preview.json()["allowed"] is True
    assert preview.json()["operations"][0]["risk"] == "high"

    unacknowledged = restore_client.post(
        "/api/restores",
        headers={"Idempotency-Key": "restore-1"},
        json={
            "preview_hash": preview.json()["preview_hash"],
            "high_risk_acknowledged": False,
        },
    )
    assert unacknowledged.status_code == 409

    confirmed = restore_client.post(
        "/api/restores",
        headers={"Idempotency-Key": "restore-1"},
        json={
            "preview_hash": preview.json()["preview_hash"],
            "high_risk_acknowledged": True,
        },
    )
    assert confirmed.status_code == 202, confirmed.text
    assert confirmed.json()["confirmed_by"] == "demo-operator"
    assert confirmed.json()["restore_request_id"] == preview.json()["restore_request_id"]
    repeated_confirmation = restore_client.post(
        "/api/restores",
        headers={"Idempotency-Key": "restore-1"},
        json={
            "preview_hash": preview.json()["preview_hash"],
            "high_risk_acknowledged": True,
        },
    )
    assert repeated_confirmation.status_code == 202
    assert repeated_confirmation.json()["batch_id"] == confirmed.json()["batch_id"]
    conflicting_confirmation = restore_client.post(
        "/api/restores",
        headers={"Idempotency-Key": "restore-other"},
        json={
            "preview_hash": preview.json()["preview_hash"],
            "high_risk_acknowledged": True,
        },
    )
    assert conflicting_confirmation.status_code == 409

    executed = restore_client.post(f"/api/restores/{preview.json()['restore_request_id']}/execute")
    assert executed.status_code == 202, executed.text
    assert executed.json()["status"] == "succeeded", executed.json()
    assert executed.json()["output_target_version_id"]

    forward_preview = restore_client.post(
        f"/api/target-versions/{detail['output_target_version_ids'][-1]}/restore-preview"
    )
    assert forward_preview.status_code == 200, forward_preview.text
    assert forward_preview.json()["source_version_id"] == executed.json()[
        "output_target_version_id"
    ]
    assert forward_preview.json()["semantic_source_version_id"] == root["id"]
    assert forward_preview.json()["covered_execution_ids"] == [batch["id"]]
    assert forward_preview.json()["operations"][0]["operation_type"] == "create"
    forward_confirmed = restore_client.post(
        "/api/restores",
        headers={"Idempotency-Key": "restore-2"},
        json={
            "preview_hash": forward_preview.json()["preview_hash"],
            "high_risk_acknowledged": True,
        },
    )
    assert forward_confirmed.status_code == 202, forward_confirmed.text
    forward_executed = restore_client.post(
        f"/api/restores/{forward_preview.json()['restore_request_id']}/execute"
    )
    assert forward_executed.status_code == 202, forward_executed.text
    assert forward_executed.json()["status"] == "succeeded"
    repeated_versions = restore_client.get(
        f"/api/reconciliation-tasks/{task_id}/target-versions"
    ).json()
    final_version = next(
        item
        for item in repeated_versions
        if item["id"] == forward_executed.json()["output_target_version_id"]
    )
    assert len(repeated_versions) == 4
    assert final_version["parent_version_id"] == executed.json()["output_target_version_id"]


def test_restore_hash_mismatch_does_not_publish_a_current_version(
    restore_client: TestClient,
) -> None:
    batch = _executed_batch(restore_client)
    detail = restore_client.get(f"/api/execution-records/{batch['id']}").json()
    task_id = detail["task_id"]
    versions = restore_client.get(f"/api/reconciliation-tasks/{task_id}/target-versions").json()
    root = next(item for item in versions if item["parent_version_id"] is None)

    async def materialize_and_corrupt_target():
        async with restore_client.app.state.database.session_factory() as session:
            repository = ExecutionRepository(session)
            root_record = await repository.get_target_version(UUID(root["id"]))
            output_record = await repository.get_target_version(
                UUID(detail["output_target_version_ids"][-1])
            )
            await asyncio.to_thread(
                Path(root_record.storage_path).write_text,
                "entity_type,id,name\n",
                encoding="utf-8",
            )
            await asyncio.to_thread(
                Path(output_record.storage_path).write_text,
                "entity_type,id,name,parent_id\n教师,t-a,张三,d-a\n",
                encoding="utf-8",
            )
            for record in (root_record, output_record):
                content = await asyncio.to_thread(Path(record.storage_path).read_bytes)
                file_hash = hashlib.sha256(content).hexdigest()
                await session.execute(
                    update(TargetVersionRecord)
                    .where(TargetVersionRecord.id == record.id)
                    .values(file_sha256=file_hash)
                )
            await session.commit()

    restore_client.portal.call(materialize_and_corrupt_target)
    preview = restore_client.post(f"/api/target-versions/{root['id']}/restore-preview").json()
    confirmed = restore_client.post(
        "/api/restores",
        headers={"Idempotency-Key": "restore-mismatch"},
        json={"preview_hash": preview["preview_hash"], "high_risk_acknowledged": True},
    )
    assert confirmed.status_code == 202

    async def corrupt_selected_history():
        async with restore_client.app.state.database.session_factory() as session:
            root_record = await ExecutionRepository(session).get_target_version(UUID(root["id"]))
            await asyncio.to_thread(
                Path(root_record.storage_path).write_text,
                "entity_type,id,name\n教师,unexpected,意外记录\n",
                encoding="utf-8",
            )

    restore_client.portal.call(corrupt_selected_history)
    response = restore_client.post(f"/api/restores/{preview['restore_request_id']}/execute")

    assert response.status_code == 202
    assert response.json()["status"] == "failed"
    remaining = restore_client.get(
        f"/api/reconciliation-tasks/{task_id}/target-versions"
    ).json()
    assert len(remaining) == 3
