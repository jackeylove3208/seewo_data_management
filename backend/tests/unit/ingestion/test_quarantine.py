from pathlib import Path

from app.ingestion.quarantine import write_quarantine
from app.schemas.ingestion import IngestionIssue


def test_quarantine_csv_contains_actionable_row_details(tmp_path: Path) -> None:
    path = write_quarantine(
        tmp_path / "quarantine.csv",
        [
            IngestionIssue(
                row_number=7,
                code="missing_required_value",
                field="name",
                message="name is required",
                original_value="",
            )
        ],
    )

    content = path.read_text(encoding="utf-8-sig")
    assert "row_number,code,field,message,original_value" in content
    assert "7,missing_required_value,name,name is required," in content
