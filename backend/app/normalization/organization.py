import re
from collections.abc import Sequence

from app.normalization.text import normalize_null

DEFAULT_GRADE_ALIASES = {
    "高中一年级": "高一",
    "高中二年级": "高二",
    "高中三年级": "高三",
    "高一年级": "高一",
    "高二年级": "高二",
    "高三年级": "高三",
}
DEFAULT_SCHOOL_YEAR_PATTERN = r"(?<!\d)(20\d{2})(?!\d)"
DEFAULT_CLASS_NUMBER_PATTERNS = (
    r"\(\s*0*(\d+)\s*\)\s*班",
    r"级\s*0*(\d+)\s*班",
    r"(?<!\d)0*(\d+)\s*班",
)


def normalize_organization_path(
    value: str | None,
    separators: Sequence[str] = ("/", ">", "\\"),
) -> str | None:
    normalized = normalize_null(value)
    if normalized is None:
        return None
    separator_pattern = "|".join(re.escape(separator) for separator in separators)
    parts = [part.strip() for part in re.split(separator_pattern, normalized) if part.strip()]
    return "/".join(parts) or None


def normalize_grade(
    value: str | None,
    aliases: dict[str, str] = DEFAULT_GRADE_ALIASES,
) -> str | None:
    normalized = normalize_null(value)
    if normalized is None:
        return None
    compact = normalized.replace(" ", "")
    return aliases.get(compact, compact)


def normalize_school_year(
    value: str | None,
    pattern: str = DEFAULT_SCHOOL_YEAR_PATTERN,
) -> str | None:
    normalized = normalize_null(value)
    if normalized is None:
        return None
    match = re.search(pattern, normalized)
    return match.group(1) if match else None


def normalize_class_number(
    value: str | None,
    patterns: Sequence[str] = DEFAULT_CLASS_NUMBER_PATTERNS,
) -> str | None:
    normalized = normalize_null(value)
    if normalized is None:
        return None
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            return str(int(match.group(1)))
    return None


def normalize_teacher_display_name(
    value: str | None,
    known_subjects: frozenset[str],
) -> tuple[str | None, str | None]:
    normalized = normalize_null(value)
    if normalized is None:
        return None, None
    match = re.fullmatch(r"(.+?)\s*\(([^()]+)\)", normalized)
    if match and match.group(2).strip() in known_subjects:
        return match.group(1).strip(), match.group(2).strip()
    return normalized, None
