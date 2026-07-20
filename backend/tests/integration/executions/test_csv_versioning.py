import csv
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.executions.csv_versioning import CsvMutationError, CsvTargetVersioner
from app.schemas.canonical_entities import EntityType
from app.schemas.executions import (
    GovernanceOperation,
    OperationType,
    ProposalSource,
    ProposalVersionRef,
    TargetVersion,
)
from app.schemas.governance import RiskLevel


class VersionRepositorySpy:
    def __init__(self) -> None:
        self.created: dict[str, object] | None = None

    async def create_target_version(self, **values):
        self.created = values
        return SimpleNamespace(id=uuid4(), **values)


def operation(
    operation_type: OperationType,
    *,
    target: str | None = None,
    before: dict[str, object] | None = None,
    after: dict[str, object] | None = None,
) -> GovernanceOperation:
    return GovernanceOperation(
        proposal=ProposalVersionRef(proposal_id=uuid4(), proposal_version=1),
        proposal_source=ProposalSource.AI,
        difference_id=uuid4(),
        difference_version=1,
        analysis_id=uuid4(),
        analysis_version="analysis-v2",
        operation_type=operation_type,
        entity_type=EntityType.TEACHER,
        target_source_identifier=target,
        before=before,
        after=after,
        changed_fields=frozenset((after or {}).keys()),
        reversible=True,
        risk=RiskLevel.MEDIUM,
    )


def parent_version(path: Path) -> TargetVersion:
    return TargetVersion(
        id=uuid4(),
        task_id=uuid4(),
        tenant_id="school-1",
        source_snapshot_id=uuid4(),
        file_sha256="a" * 64,
        content_hash="b" * 64,
        storage_path=str(path),
        created_at=datetime.now(UTC),
    )


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.mark.asyncio
async def test_derive_child_applies_operations_and_preserves_original(
    tmp_path: Path,
) -> None:
    original = tmp_path / "uploaded-target.csv"
    original.write_text(
        "entity_type,id,name,parent_id,phone,status,custom\n"
        "教师,T1,Existing,D1,13900000000,active,keep-me\n",
        encoding="utf-8",
    )
    original_bytes = original.read_bytes()
    repository = VersionRepositorySpy()
    versioner = CsvTargetVersioner(repository=repository, output_root=tmp_path / "derived")
    batch_id = uuid4()
    operations = (
        operation(
            OperationType.UPDATE,
            target="T1",
            before={"phone": "13900000000"},
            after={"phone": "13800000000"},
        ),
        operation(
            OperationType.MOVE,
            target="T1",
            before={"department_source_id": "D1"},
            after={"department_source_id": "D2"},
        ),
        operation(
            OperationType.DISABLE,
            target="T1",
            before={"status": "active"},
            after={"status": "disabled"},
        ),
        operation(
            OperationType.CREATE,
            after={
                "source_id": "T2",
                "name": '=HYPERLINK("https://bad.example")',
                "phone": "13700000000",
            },
        ),
    )

    child = await versioner.derive(parent_version(original), operations, batch_id=batch_id)

    assert original.read_bytes() == original_bytes
    assert Path(child.storage_path) != original
    rows = {row["id"]: row for row in read_rows(Path(child.storage_path))}
    assert rows["T1"]["phone"] == "13800000000"
    assert rows["T1"]["parent_id"] == "D2"
    assert rows["T1"]["status"] == "disabled"
    assert rows["T1"]["custom"] == "keep-me"
    assert rows["T2"]["name"].startswith("'=HYPERLINK")
    assert repository.created is not None
    assert repository.created["parent_version_id"] == child.parent_version_id
    assert repository.created["batch_id"] == batch_id
    assert len(str(repository.created["file_sha256"])) == 64


@pytest.mark.asyncio
async def test_derive_rejects_missing_target_without_writing_child(tmp_path: Path) -> None:
    original = tmp_path / "uploaded-target.csv"
    original.write_text(
        "entity_type,id,name\n教师,T1,Existing\n",
        encoding="utf-8",
    )
    output_root = tmp_path / "derived"
    versioner = CsvTargetVersioner(
        repository=VersionRepositorySpy(),
        output_root=output_root,
    )

    with pytest.raises(CsvMutationError, match="target row"):
        await versioner.derive(
            parent_version(original),
            (
                operation(
                    OperationType.UPDATE,
                    target="missing",
                    before={"name": "Old"},
                    after={"name": "New"},
                ),
            ),
            batch_id=uuid4(),
        )

    assert not output_root.exists() or not tuple(output_root.iterdir())
