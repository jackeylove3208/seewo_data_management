import asyncio
import csv
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import Settings
from app.main import create_app
from app.models.snapshots import SourceFile
from app.schemas.canonical_entities import SourceRole
from tests.integration.api.test_ingestion_api import upload, valid_task_payload


@pytest.fixture
def governance_client(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'governance-e2e.db'}",
        upload_root=tmp_path / "uploads",
        snapshot_root=tmp_path / "snapshots",
        quarantine_root=tmp_path / "quarantine",
        export_root=tmp_path / "exports",
        auto_create_schema=True,
    )
    with TestClient(create_app(settings)) as client:
        yield client


def test_reviewed_manual_proposal_produces_verified_derived_csv(
    governance_client: TestClient,
    tmp_path: Path,
) -> None:
    header = (
        "entity_type,id,name,parent_id,grade,class_name,subject,employee_number,phone,email,extra\n"
    )
    source_path = tmp_path / "source.csv"
    source_path.write_text(
        header
        + "部门,D01,教务处,,,,,,,,\n"
        + "教师,T01,张三,D01,,,数学,E001,13100000000,,source-extra\n",
        encoding="utf-8",
    )
    target_path = tmp_path / "target.csv"
    target_path.write_text(
        header
        + "部门,D01,教务处,,,,,,,,\n"
        + "教师,T01,张三,D01,,,数学,E001,13000000000,,target-extra\n",
        encoding="utf-8",
    )
    source = upload(governance_client, source_path, "authoritative")
    target = upload(governance_client, target_path, "target")
    created = governance_client.post(
        "/api/reconciliation-tasks",
        json=valid_task_payload(source["id"], target["id"]),
        headers={"Idempotency-Key": "governance-e2e-task"},
    )
    assert created.status_code == 202, created.text
    task_id = created.json()["id"]
    for _stage in range(3):
        advanced = governance_client.post(f"/api/reconciliation-tasks/{task_id}/workflow/advance")
        assert advanced.status_code == 200, advanced.text
    differences = governance_client.get(
        f"/api/reconciliation-tasks/{task_id}/differences",
        params={"difference_type": "attribute_conflict"},
    )
    assert differences.status_code == 200, differences.text
    assert differences.json()["items"], differences.text
    difference = differences.json()["items"][0]
    proposal = governance_client.post(
        f"/api/differences/{difference['id']}/proposals/manual",
        json={
            "expected_difference_version": difference["version"],
            "operation_type": "update",
            "target_entity_id": difference["evidence"]["target_entity_id"],
            "changes": {"phone": "13100000000"},
            "rationale": "The operator confirmed the authoritative phone value",
        },
    )
    assert proposal.status_code == 201, proposal.text
    preview = governance_client.post(
        "/api/execution-batches/preview",
        json={
            "task_id": task_id,
            "proposals": [
                {
                    "proposal_id": proposal.json()["id"],
                    "proposal_version": proposal.json()["proposal_version"],
                }
            ],
        },
    )
    assert preview.status_code == 200, preview.text
    confirmed = governance_client.post(
        "/api/execution-batches",
        headers={"Idempotency-Key": "governance-e2e-batch"},
        json={
            "plan_id": preview.json()["plan_id"],
            "plan_version": preview.json()["plan_version"],
        },
    )
    assert confirmed.status_code == 202, confirmed.text

    async def uploaded_target_bytes() -> bytes:
        async with governance_client.app.state.database.session_factory() as session:
            record = await session.scalar(
                select(SourceFile).where(
                    SourceFile.id == UUID(str(target["id"])),
                    SourceFile.source_role == SourceRole.TARGET.value,
                )
            )
            assert record is not None
            return await asyncio.to_thread(Path(record.storage_path).read_bytes)

    assert governance_client.portal is not None
    original_bytes = governance_client.portal.call(uploaded_target_bytes)
    executed = governance_client.post(f"/api/execution-batches/{confirmed.json()['id']}/execute")

    assert executed.status_code == 202, executed.text
    assert executed.json()["status"] == "succeeded"
    assert executed.json()["operations"][0]["status"] == "succeeded"
    assert governance_client.portal.call(uploaded_target_bytes) == original_bytes
    downloaded = governance_client.get(
        f"/api/execution-records/{confirmed.json()['id']}/target-version"
    )
    assert downloaded.status_code == 200, downloaded.text
    rows = list(csv.DictReader(downloaded.content.decode("utf-8-sig").splitlines()))
    teacher = next(row for row in rows if row["id"] == "T01")
    assert teacher["phone"] == "13100000000"
    assert teacher["extra"] == "target-extra"
    detail = governance_client.get(f"/api/execution-records/{confirmed.json()['id']}").json()
    assert detail["status"] == "succeeded"
    assert detail["operations"][0]["attempts"][0]["verification"]["valid"] is True
