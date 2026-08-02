import pytest

from app.normalization.identifiers import (
    normalize_email,
    normalize_identifier,
    normalize_phone,
)
from app.normalization.text import normalize_null, normalize_status, normalize_whitespace


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(" 张  三 ", "张 三"), ("Ａ班", "A班"), (None, None)],
)
def test_whitespace_and_nfkc(raw: str | None, expected: str | None) -> None:
    assert normalize_whitespace(raw) == expected


@pytest.mark.parametrize("raw", ["", " N/A ", "null", "-", None])
def test_null_tokens_are_normalized(raw: str | None) -> None:
    assert normalize_null(raw) is None


def test_identifiers_are_normalized_without_fuzzy_changes() -> None:
    assert normalize_identifier(" e-007 ") == "E-007"
    assert normalize_phone("+86 138-0000-0000") == "13800000000"
    assert normalize_email(" Teacher@Example.COM ") == "teacher@example.com"


def test_phone_normalization_accepts_landline_numbers() -> None:
    assert normalize_phone("010-1234-5678") == "01012345678"


def test_malformed_contact_identifiers_are_not_matchable() -> None:
    assert normalize_phone("123") is None
    assert normalize_phone("1234567890123456") is None
    assert normalize_phone("not-a-phone") is None
    assert normalize_email("foo") is None
    assert normalize_email("teacher@example") is None


def test_status_aliases_are_versioned_rules_not_free_text() -> None:
    assert normalize_status(" 启用 ") == "active"
    assert normalize_status("停用") == "inactive"
    assert normalize_status("待审核") == "待审核"
