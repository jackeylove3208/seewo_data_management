from pathlib import Path
from uuid import uuid4

import pytest

from app.ingestion.agent_contract import AgentContractError
from app.ingestion.agent_csv_adapter import AgentCsvIngestionAdapter
from app.schemas.agent_ingestion import AgentEntityKind, AgentSourceRole


def test_inspects_csv_in_physical_order_with_selected_entities(tmp_path: Path) -> None:
    path = tmp_path / "authority.csv"
    path.write_text(
        "category,name,number,class,phone,email\n"
        "teacher,张三,T-1,,13800138001,t@example.com\n"
        "student,李四,S-1,一班,13800138002,s@example.com\n"
        "department,教务处,D-1,,13800138003,d@example.com\n",
        encoding="utf-8",
    )

    outcome = AgentCsvIngestionAdapter().inspect_csv(
        path=path, task_id=uuid4(), run_id=uuid4(), snapshot_id=uuid4(), tenant_id="school-1",
        source_role=AgentSourceRole.AUTHORITATIVE,
        selected_entities=frozenset({AgentEntityKind.STUDENT, AgentEntityKind.TEACHER}),
    )

    assert [record.raw_row_number for record in outcome.records] == [2, 3]
    assert [record.stable_order for record in outcome.records] == [1, 2]
    assert outcome.marks == ()


def test_collects_safe_authority_marks(tmp_path: Path) -> None:
    path = tmp_path / "authority.csv"
    path.write_text(
        "类别,姓名,编号,班级,电话,邮箱\n学生,李四,S-1,一班,13800138000,\n",
        encoding="utf-8",
    )

    outcome = AgentCsvIngestionAdapter().inspect_csv(
        path=path, task_id=uuid4(), run_id=uuid4(), snapshot_id=uuid4(), tenant_id="school-1",
        source_role=AgentSourceRole.AUTHORITATIVE,
        selected_entities=frozenset({AgentEntityKind.STUDENT}),
    )

    assert outcome.marks[0].reason_code == "authority_required_fields_missing"
    assert "13800138000" not in str(outcome.marks[0].safe_evidence)


def test_rejects_unrecognizable_csv_schema(tmp_path: Path) -> None:
    path = tmp_path / "broken.csv"
    path.write_text("foo,bar\na,b\n", encoding="utf-8")

    with pytest.raises(AgentContractError, match="unrecognizable"):
        AgentCsvIngestionAdapter().inspect_csv(
            path=path, task_id=uuid4(), run_id=uuid4(), snapshot_id=uuid4(), tenant_id="school-1",
            source_role=AgentSourceRole.TARGET, selected_entities=frozenset(AgentEntityKind),
        )
