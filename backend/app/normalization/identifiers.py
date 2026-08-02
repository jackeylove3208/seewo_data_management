import re

from app.normalization.text import normalize_null


def normalize_identifier(value: str | None) -> str | None:
    normalized = normalize_null(value)
    return normalized.upper() if normalized else None


def normalize_phone(value: str | None) -> str | None:
    digits = re.sub(r"\D", "", normalize_null(value) or "")
    if digits.startswith("86") and len(digits) == 13:
        digits = digits[2:]
    return digits if re.fullmatch(r"\d{7,15}", digits) else None


def normalize_email(value: str | None) -> str | None:
    normalized = normalize_null(value)
    if normalized is None:
        return None
    normalized = normalized.casefold()
    return normalized if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized) else None
