import re
import unicodedata
from collections.abc import Mapping

NULL_TOKENS = frozenset({"", "null", "none", "n/a", "-"})
DEFAULT_STATUS_ALIASES = {
    "active": "active",
    "enabled": "active",
    "启用": "active",
    "在用": "active",
    "inactive": "inactive",
    "disabled": "inactive",
    "停用": "inactive",
    "禁用": "inactive",
}


def normalize_unicode(value: str | None) -> str | None:
    return unicodedata.normalize("NFKC", value) if value is not None else None


def normalize_whitespace(value: str | None) -> str | None:
    normalized = normalize_unicode(value)
    return re.sub(r"\s+", " ", normalized).strip() if normalized is not None else None


def normalize_null(value: str | None) -> str | None:
    normalized = normalize_whitespace(value)
    if normalized is None or normalized.casefold() in NULL_TOKENS:
        return None
    return normalized


def normalize_status(
    value: str | None,
    aliases: Mapping[str, str] = DEFAULT_STATUS_ALIASES,
) -> str | None:
    normalized = normalize_null(value)
    if normalized is None:
        return None
    return aliases.get(normalized.casefold(), normalized)
