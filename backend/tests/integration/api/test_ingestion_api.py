from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app

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
        "tenant_id": "school-1",
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
            "tenant_id": "school-1",
            "scope_id": "all",
            "snapshot_mode": "full",
            "authoritative_mapping_version": "third-party-v1",
            "target_mapping_version": "mofa-v1",
        },
        headers={"Idempotency-Key": "missing-target"},
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"][-1] == "target_upload_id"


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
