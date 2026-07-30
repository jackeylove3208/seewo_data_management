import hashlib
import json
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from app.ingestion.agent_api_adapter import (
    AgentApiIngestionAdapter,
    ApiArtifactBinding,
)
from app.repositories.agent_analysis import ReplayConflict
from app.schemas.agent_ingestion import AgentEntityKind


def _artifact(
    tmp_path: Path,
    *,
    external_id: str = "ding-user-7",
    task_id=None,
    tenant_id: str = "school-1",
    api_source_id=None,
    connection_id=None,
    source_file_id=None,
    snapshot_id=None,
) -> tuple[Path, ApiArtifactBinding]:
    task_id = task_id or uuid4()
    api_source_id = api_source_id or uuid4()
    connection_id = connection_id or uuid4()
    source_file_id = source_file_id or uuid4()
    snapshot_id = snapshot_id or uuid4()
    selection_hash = "a" * 64
    header = {
        "record_type": "header",
        "contract_version": "api-authority-jsonl-v1",
        "task_id": str(task_id),
        "tenant_id": tenant_id,
        "api_source_id": str(api_source_id),
        "connection_id": str(connection_id),
        "provider_id": "dingtalk",
        "source_file_id": str(source_file_id),
        "snapshot_id": str(snapshot_id),
        "selected_entities": ["teacher"],
        "selection_hash": selection_hash,
        "manifest_version": "2026-07-29",
        "adapter_version": "1.0.0",
        "projection_version": "organization-six-fields-v1",
        "page_count": 1,
        "record_count": 1,
    }
    record = {
        "record_type": "record",
        "external_id": external_id,
        "entity_kind": "teacher",
        "provider_fields": {
            "userid": external_id,
            "name": " 周明远 ",
        },
        "projected_fields": {
            "category": "教师",
            "name": " 周明远 ",
            "number": None,
            "class_name": None,
            "phone": "138 0000 0001",
            "email": None,
        },
        "unavailable_fields": ["email", "number"],
    }
    content = "".join(
        json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
        for item in (header, record)
    ).encode()
    path = tmp_path / "authority.jsonl"
    path.write_bytes(content)
    binding = ApiArtifactBinding(
        task_id=task_id,
        tenant_id=tenant_id,
        api_source_id=api_source_id,
        connection_id=connection_id,
        provider_id="dingtalk",
        source_file_id=source_file_id,
        snapshot_id=snapshot_id,
        selection_hash=selection_hash,
        selected_entities=frozenset({AgentEntityKind.TEACHER}),
        manifest_version="2026-07-29",
        adapter_version="1.0.0",
        projection_version="organization-six-fields-v1",
        content_sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
    )
    return path, binding


async def test_api_adapter_projects_frozen_records_without_using_external_id_as_number(
    tmp_path: Path,
) -> None:
    path, binding = _artifact(tmp_path)
    run_id = uuid4()

    outcome = await AgentApiIngestionAdapter().extract(
        path=path,
        run_id=run_id,
        binding=binding,
    )

    assert len(outcome.records) == 1
    record = outcome.records[0]
    assert record.task_id == binding.task_id
    assert record.run_id == run_id
    assert record.snapshot_id == binding.snapshot_id
    assert record.stable_locator == (
        f"api:{binding.connection_id}:teacher:ding-user-7"
    )
    assert record.stable_order == 1
    assert record.raw_row_number is None
    assert record.name == "周明远"
    assert record.number is None
    assert record.phone == "13800000001"
    assert record.email is None
    assert [mark.reason_code for mark in outcome.marks] == [
        "authority_field_unavailable"
    ]
    assert outcome.marks[0].affected_fields == ("email", "number")
    assert outcome.marks[0].inclusion_state == "included"


async def test_api_adapter_rejects_cross_task_header_even_when_file_hash_matches(
    tmp_path: Path,
) -> None:
    path, binding = _artifact(tmp_path)
    changed = replace(binding, task_id=uuid4())

    with pytest.raises(ReplayConflict, match="header"):
        await AgentApiIngestionAdapter().extract(
            path=path,
            run_id=uuid4(),
            binding=changed,
        )


async def test_api_adapter_rejects_modified_frozen_artifact(tmp_path: Path) -> None:
    path, binding = _artifact(tmp_path)
    path.write_text(path.read_text().replace("周明远", "被篡改"), encoding="utf-8")

    with pytest.raises(ReplayConflict, match="integrity"):
        await AgentApiIngestionAdapter().extract(
            path=path,
            run_id=uuid4(),
            binding=binding,
        )
