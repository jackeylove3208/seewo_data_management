import csv
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.executions import csv_versioning
from app.executions.agent_service import AgentExecutionService, CsvAgentTargetAdapter
from app.executions.csv_versioning import (
    CsvMutationError,
    CsvTargetVersioner,
)
from app.governance.agent_governance import (
    AgentGovernanceOperation,
    AgentOperation,
)
from app.ingestion.agent_csv_adapter import AgentCsvIngestionAdapter
from app.schemas.agent_ingestion import AgentEntityKind, AgentSourceRole
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


def test_read_target_rows_preserves_raw_values_under_stable_locators(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target.csv"
    target.write_text(
        "类别,姓名,电话\n教师,测试教师,+86 13800138000\n",
        encoding="utf-8",
    )

    rows = csv_versioning.read_target_rows(target)

    assert rows["csv:2"]["category"] == "教师"
    assert rows["csv:2"]["phone"] == "+86 13800138000"


@pytest.mark.asyncio
async def test_generated_locator_survives_an_earlier_row_deletion(
    tmp_path: Path,
) -> None:
    original = tmp_path / "target.csv"
    original.write_text(
        "category,name,number,phone,email\n"
        "teacher,甲,T-1,13800138001,t1@example.test\n"
        "teacher,乙,T-2,13800138002,t2@example.test\n",
        encoding="utf-8",
    )
    first_versioner = CsvTargetVersioner(
        repository=VersionRepositorySpy(),
        output_root=tmp_path / "first",
    )
    child = await first_versioner.derive(
        parent_version(original),
        (
            operation(
                OperationType.DISABLE,
                target="csv:2",
                before={"number": "T-1"},
                after={},
            ).model_copy(
                update={
                    "compensation_for": uuid4(),
                    "restore_absence": True,
                }
            ),
        ),
        batch_id=uuid4(),
    )

    outcome = AgentCsvIngestionAdapter().inspect_csv(
        path=Path(child.storage_path),
        task_id=uuid4(),
        run_id=uuid4(),
        snapshot_id=uuid4(),
        tenant_id="school-1",
        source_role=AgentSourceRole.TARGET,
        selected_entities=frozenset({AgentEntityKind.TEACHER}),
    )

    assert outcome.records[0].stable_locator == "csv:3"

    second_versioner = CsvTargetVersioner(
        repository=VersionRepositorySpy(),
        output_root=tmp_path / "second",
    )
    updated = await second_versioner.derive(
        child,
        (
            operation(
                OperationType.UPDATE,
                target="csv:3",
                before={"name": "乙"},
                after={"name": "乙老师"},
            ),
        ),
        batch_id=uuid4(),
    )

    assert read_rows(Path(updated.storage_path))[0]["name"] == "乙老师"


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


@pytest.mark.asyncio
async def test_agent_csv_precondition_failure_becomes_a_safe_operation_outcome(
    tmp_path: Path,
) -> None:
    original = tmp_path / "uploaded-target.csv"
    original.write_text(
        "类别,姓名\n教师,测试教师\n",
        encoding="utf-8",
    )
    parent = parent_version(original)
    governance_operation = AgentGovernanceOperation(
        id=uuid4(),
        finding_id=uuid4(),
        operation=AgentOperation.UPDATE,
        entity_kind="teacher",
        target_source_identifier="csv:2",
        before={"category": "老师"},
        after={"category": "教师"},
        dependencies=frozenset(),
        risk="medium",
        target_version=f"sha256:{parent.file_sha256}",
    )

    result = await AgentExecutionService().execute(
        plan_id=uuid4(),
        target_version=governance_operation.target_version,
        operations=(governance_operation,),
        target=CsvAgentTargetAdapter(
            versioner=CsvTargetVersioner(
                repository=VersionRepositorySpy(),
                output_root=tmp_path / "derived",
            ),
            parent=parent,
        ),
    )

    assert result.status == "failed"
    assert result.by_operation[governance_operation.id].error_code == "AgentTargetError"


@pytest.mark.asyncio
async def test_delete_creates_verified_child_without_overwriting_parent(tmp_path: Path) -> None:
    original = tmp_path / "uploaded-target.csv"
    original.write_text("entity_type,id,name\n学生,S1,Ada\n学生,S2,Grace\n", encoding="utf-8")
    original_bytes = original.read_bytes()
    repository = VersionRepositorySpy()
    versioner = CsvTargetVersioner(repository=repository, output_root=tmp_path / "derived")

    child = await versioner.derive(
        parent_version(original),
        (
            operation(
                OperationType.DISABLE,
                target="S1",
                before={"name": "Ada"},
                after={},
            ).model_copy(
                update={"after": {}, "compensation_for": uuid4(), "restore_absence": True}
            ),
        ),
        batch_id=uuid4(),
    )

    assert original.read_bytes() == original_bytes
    assert read_rows(Path(child.storage_path)) == [
        {"entity_type": "学生", "id": "S2", "name": "Grace"}
    ]
