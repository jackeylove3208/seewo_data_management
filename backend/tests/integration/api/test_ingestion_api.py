from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.dependencies import get_operator_context
from app.core.config import Settings
from app.core.security import OperatorContext
from app.main import create_app
from app.models.analyses import AnalysisRecord
from app.models.differences import DifferenceRecord
from app.models.mappings import EntityMapping

ROOT = Path(__file__).parents[4]


@pytest.fixture
def client(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'api.db'}",
        upload_root=tmp_path / "uploads",
        snapshot_root=tmp_path / "snapshots",
        quarantine_root=tmp_path / "quarantine",
        auto_create_schema=True,
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def upload(client: TestClient, path: Path, role: str) -> dict:
    with path.open("rb") as handle:
        response = client.post(
            "/api/uploads",
            data={"source_role": role},
            files={"file": (path.name, handle, "text/csv")},
        )
    assert response.status_code == 201, response.text
    return response.json()


def valid_task_payload(source_id: str, target_id: str) -> dict:
    return {
        "authoritative_upload_id": source_id,
        "target_upload_id": target_id,
        "scope_id": "all",
        "snapshot_mode": "full",
        "schema_version": "canonical-v1",
        "authoritative_mapping_version": "third-party-v1",
        "target_mapping_version": "mofa-v1",
    }


def test_real_csv_upload_preview_and_snapshot_creation(client: TestClient) -> None:
    source = upload(client, ROOT / "third_party_data.csv", "authoritative")
    target = upload(client, ROOT / "mofa_data.csv", "target")

    assert source["original_name"] == "third_party_data.csv"
    assert source["size_bytes"] == (ROOT / "third_party_data.csv").stat().st_size
    assert len(source["sha256"]) == 64
    preview = client.post(
        f"/api/uploads/{source['id']}/mapping-preview",
        json={"mapping_version": "third-party-v1"},
    )
    assert preview.status_code == 200
    assert preview.json()["sample_rows"][0]["entity_type"] == "部门"
    assert preview.json()["mapped_columns"]["source_id"] == "id"

    payload = valid_task_payload(source["id"], target["id"])
    created = client.post(
        "/api/reconciliation-tasks",
        json=payload,
        headers={"Idempotency-Key": "real-pair-1"},
    )

    assert created.status_code == 202, created.text
    body = created.json()
    assert body["tenant_id"] == "school-1"
    assert body["workflow"]["stage"] == "matching"
    assert body["workflow"]["status"] == "pending"
    assert body["status"] == "ready"
    assert body["snapshots"]["authoritative"]["accepted"] == 515
    assert body["snapshots"]["target"]["accepted"] == 518
    assert body["snapshots"]["authoritative"]["mapping_version"] == "third-party-v1"
    fetched = client.get(f"/api/reconciliation-tasks/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == body

    replay = client.post(
        "/api/reconciliation-tasks",
        json=payload,
        headers={"Idempotency-Key": "real-pair-1"},
    )
    assert replay.status_code == 202
    assert replay.json()["id"] == body["id"]


def test_task_creation_requires_both_uploads(client: TestClient) -> None:
    source = upload(client, ROOT / "third_party_data.csv", "authoritative")

    response = client.post(
        "/api/reconciliation-tasks",
        json={
            "authoritative_upload_id": source["id"],
            "scope_id": "all",
            "snapshot_mode": "full",
            "authoritative_mapping_version": "third-party-v1",
            "target_mapping_version": "mofa-v1",
        },
        headers={"Idempotency-Key": "missing-target"},
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"][-1] == "target_upload_id"


def test_task_creation_rejects_client_supplied_tenant(client: TestClient) -> None:
    source = upload(client, ROOT / "third_party_data.csv", "authoritative")
    target = upload(client, ROOT / "mofa_data.csv", "target")
    payload = {**valid_task_payload(source["id"], target["id"]), "tenant_id": "spoofed"}

    response = client.post(
        "/api/reconciliation-tasks",
        json=payload,
        headers={"Idempotency-Key": "spoofed-tenant"},
    )

    assert response.status_code == 422


def test_cross_tenant_task_access_is_hidden(client: TestClient) -> None:
    source = upload(client, ROOT / "third_party_data.csv", "authoritative")
    target = upload(client, ROOT / "mofa_data.csv", "target")
    created = client.post(
        "/api/reconciliation-tasks",
        json=valid_task_payload(source["id"], target["id"]),
        headers={"Idempotency-Key": "tenant-guard"},
    )
    assert created.status_code == 202
    task_id = created.json()["id"]

    client.app.dependency_overrides[get_operator_context] = lambda: OperatorContext(
        operator_id="other-operator",
        tenant_id="other-school",
    )
    try:
        assert client.get(f"/api/reconciliation-tasks/{task_id}").status_code == 404
        assert client.post(f"/api/reconciliation-tasks/{task_id}/resolve").status_code == 404
        assert (
            client.post(f"/api/reconciliation-tasks/{task_id}/workflow/advance").status_code == 404
        )
    finally:
        client.app.dependency_overrides.pop(get_operator_context, None)


def test_upload_rejects_unsupported_encoding(client: TestClient, tmp_path: Path) -> None:
    path = tmp_path / "utf16.csv"
    path.write_bytes("entity_type,id,name\n部门,D01,教务处\n".encode("utf-16"))

    with path.open("rb") as handle:
        response = client.post(
            "/api/uploads",
            data={"source_role": "authoritative"},
            files={"file": (path.name, handle, "text/csv")},
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_csv"


def test_quarantined_rows_are_reported_and_downloadable(
    client: TestClient,
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.csv"
    source_path.write_text(
        "entity_type,id,name,parent_id,grade,class_name,subject,phone,email,extra\n"
        "部门,D01,教务处,,,,,,,\n"
        "未知,X01,未知实体,,,,,,,\n",
        encoding="utf-8",
    )
    target_path = tmp_path / "target.csv"
    target_path.write_text(
        "entity_type,id,name,parent_id,grade,class_name,subject,phone,email,extra\n"
        "部门,D01,教务处,,,,,,,\n",
        encoding="utf-8",
    )
    source = upload(client, source_path, "authoritative")
    target = upload(client, target_path, "target")

    response = client.post(
        "/api/reconciliation-tasks",
        json=valid_task_payload(source["id"], target["id"]),
        headers={"Idempotency-Key": "quarantine-pair"},
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["snapshots"]["authoritative"]["accepted"] == 1
    assert body["snapshots"]["authoritative"]["quarantined"] == 1
    download = client.get(f"/api/reconciliation-tasks/{body['id']}/quarantine/authoritative")
    assert download.status_code == 200
    assert "unknown_entity_type" in download.content.decode("utf-8-sig")


def test_idempotency_key_rejects_a_different_request(client: TestClient) -> None:
    source = upload(client, ROOT / "third_party_data.csv", "authoritative")
    target = upload(client, ROOT / "mofa_data.csv", "target")
    first = valid_task_payload(source["id"], target["id"])
    response = client.post(
        "/api/reconciliation-tasks",
        json=first,
        headers={"Idempotency-Key": "conflicting-key"},
    )
    assert response.status_code == 202

    second = {**first, "scope_id": "different"}
    conflict = client.post(
        "/api/reconciliation-tasks",
        json=second,
        headers={"Idempotency-Key": "conflicting-key"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "idempotency_conflict"


def test_workflow_api_advances_to_analysis_ready(client: TestClient, tmp_path: Path) -> None:
    header = "entity_type,id,name,parent_id,grade,class_name,subject,phone,email,extra\n"
    source_path = tmp_path / "workflow-source.csv"
    source_path.write_text(
        header + "部门,D01,教务处,,,,,,,\n" + "教师,T01,张三,D01,,,数学,13100000000,,\n",
        encoding="utf-8",
    )
    target_path = tmp_path / "workflow-target.csv"
    target_path.write_text(
        header + "部门,D01,教务处,,,,,,,\n" + "教师,T01,张三,D01,,,数学,13000000000,,\n",
        encoding="utf-8",
    )
    source = upload(client, source_path, "authoritative")
    target = upload(client, target_path, "target")
    created = client.post(
        "/api/reconciliation-tasks",
        json=valid_task_payload(source["id"], target["id"]),
        headers={"Idempotency-Key": "workflow-api"},
    )
    task_id = created.json()["id"]

    matching = client.post(f"/api/reconciliation-tasks/{task_id}/workflow/advance")
    differences = client.post(f"/api/reconciliation-tasks/{task_id}/workflow/advance")
    analysis = client.post(f"/api/reconciliation-tasks/{task_id}/workflow/advance")

    assert matching.status_code == 200, matching.text
    assert matching.json()["workflow"]["stage"] == "differences"
    assert differences.status_code == 200, differences.text
    assert differences.json()["workflow"]["stage"] == "analysis"
    assert analysis.status_code == 200, analysis.text
    assert analysis.json()["workflow"]["stage"] == "complete"
    assert analysis.json()["workflow"]["analysis"]["completed"] >= 1


def test_concurrent_workflow_advancement_does_not_duplicate_stage_outputs(
    client: TestClient,
    tmp_path: Path,
) -> None:
    header = "entity_type,id,name,parent_id,grade,class_name,subject,phone,email,extra\n"
    source_path = tmp_path / "concurrent-source.csv"
    source_path.write_text(
        header + "部门,D01,教务处,,,,,,,\n" + "教师,T01,张三,D01,,,数学,13100000000,,\n",
        encoding="utf-8",
    )
    target_path = tmp_path / "concurrent-target.csv"
    target_path.write_text(
        header + "部门,D01,教务处,,,,,,,\n" + "教师,T01,张三,D01,,,数学,13000000000,,\n",
        encoding="utf-8",
    )
    source = upload(client, source_path, "authoritative")
    target = upload(client, target_path, "target")
    created = client.post(
        "/api/reconciliation-tasks",
        json=valid_task_payload(source["id"], target["id"]),
        headers={"Idempotency-Key": "concurrent-workflow-api"},
    )
    task_id = created.json()["id"]
    task_uuid = UUID(task_id)

    def advance() -> int:
        return client.post(f"/api/reconciliation-tasks/{task_id}/workflow/advance").status_code

    statuses: list[int] = []
    for _wave in range(3):
        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses.extend(executor.map(lambda _index: advance(), range(2)))

    assert all(status in {200, 409} for status in statuses)

    async def duplicate_counts() -> tuple[int, int, int]:
        async with client.app.state.database.session_factory() as session:
            mapping_duplicates = await session.scalar(
                select(func.count()).select_from(
                    select(EntityMapping.source_entity_id)
                    .where(EntityMapping.task_id == task_uuid)
                    .group_by(EntityMapping.source_entity_id)
                    .having(func.count() > 1)
                    .subquery()
                )
            )
            difference_duplicates = await session.scalar(
                select(func.count()).select_from(
                    select(DifferenceRecord.evidence_hash)
                    .where(DifferenceRecord.task_id == task_uuid)
                    .group_by(DifferenceRecord.evidence_hash)
                    .having(func.count() > 1)
                    .subquery()
                )
            )
            analysis_duplicates = await session.scalar(
                select(func.count()).select_from(
                    select(
                        AnalysisRecord.difference_id,
                        AnalysisRecord.difference_version,
                        AnalysisRecord.analysis_version,
                    )
                    .join(DifferenceRecord, DifferenceRecord.id == AnalysisRecord.difference_id)
                    .where(DifferenceRecord.task_id == task_uuid)
                    .group_by(
                        AnalysisRecord.difference_id,
                        AnalysisRecord.difference_version,
                        AnalysisRecord.analysis_version,
                    )
                    .having(func.count() > 1)
                    .subquery()
                )
            )
            return (
                int(mapping_duplicates or 0),
                int(difference_duplicates or 0),
                int(analysis_duplicates or 0),
            )

    assert client.portal is not None
    assert client.portal.call(duplicate_counts) == (0, 0, 0)
