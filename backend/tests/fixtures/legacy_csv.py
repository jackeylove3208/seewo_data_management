import csv
from pathlib import Path

LEGACY_CSV_HEADERS = (
    "entity_type",
    "id",
    "name",
    "parent_id",
    "grade",
    "class_name",
    "subject",
    "phone",
    "email",
    "extra",
)


def write_legacy_csv_pair(directory: Path) -> tuple[Path, Path]:
    """Create deterministic, synthetic legacy fixtures with the historical row counts."""
    authoritative = directory / "third_party_data.csv"
    target = directory / "mofa_data.csv"
    _write_fixture(authoritative, student_count=512)
    _write_fixture(target, student_count=515)
    return authoritative, target


def _write_fixture(path: Path, *, student_count: int) -> None:
    rows = [
        ("部门", "D01", "教务处", "", "", "", "", "", "", ""),
        ("班级", "C01", "高一(1)班", "D01", "高一", "高一(1)班", "", "", "", ""),
        (
            "教师",
            "T001",
            "张老师",
            "D01",
            "",
            "",
            "语文",
            "13900000001",
            "teacher@example.edu.cn",
            "",
        ),
    ]
    rows.extend(
        (
            "学生",
            f"S{index:04d}",
            f"测试学生{index:04d}",
            "C01",
            "高一",
            "高一(1)班",
            "",
            f"138{index:08d}",
            f"student{index:04d}@example.edu.cn",
            "",
        )
        for index in range(1, student_count + 1)
    )
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(LEGACY_CSV_HEADERS)
        writer.writerows(rows)
