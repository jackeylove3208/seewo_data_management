import csv
from collections.abc import Iterable
from pathlib import Path

from app.schemas.ingestion import IngestionIssue


def write_quarantine(path: Path, issues: Iterable[IngestionIssue]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("row_number", "code", "field", "message", "original_value"),
        )
        writer.writeheader()
        for issue in issues:
            writer.writerow(
                {
                    "row_number": issue.row_number,
                    "code": issue.code,
                    "field": issue.field,
                    "message": issue.message,
                    "original_value": issue.original_value,
                }
            )
    return path
