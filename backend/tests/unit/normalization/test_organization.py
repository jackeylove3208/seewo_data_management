import pytest

from app.normalization.organization import (
    normalize_class_number,
    normalize_grade,
    normalize_organization_path,
    normalize_school_year,
    normalize_teacher_display_name,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("本部 > 教务处 / 高一年级组", "本部/教务处/高一年级组"),
        ("本部\\德育处", "本部/德育处"),
        (None, None),
    ],
)
def test_organization_paths_use_one_separator(raw: str | None, expected: str | None) -> None:
    assert normalize_organization_path(raw, ("/", ">", "\\")) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(" 高一 ", "高一"), ("高中一年级", "高一"), ("一年级", "一年级"), (None, None)],
)
def test_grade_aliases_are_normalized(raw: str | None, expected: str | None) -> None:
    assert normalize_grade(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("2024学年", "2024"), ("2024级1班", "2024"), ("高一(1)班", None), (None, None)],
)
def test_school_year_is_extracted_when_present(raw: str | None, expected: str | None) -> None:
    assert normalize_school_year(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("高一(1)班", "1"), ("2024级01班", "1"), ("高二（12）班", "12"), (None, None)],
)
def test_class_number_is_extracted(raw: str | None, expected: str | None) -> None:
    assert normalize_class_number(raw) == expected


def test_teacher_suffix_is_removed_only_for_known_subject() -> None:
    subjects = frozenset({"语文", "数学"})

    assert normalize_teacher_display_name("张三（语文）", subjects) == ("张三", "语文")
    assert normalize_teacher_display_name("李四（新入职）", subjects) == (
        "李四(新入职)",
        None,
    )
