import hashlib
import hmac
import re
import unicodedata
from enum import StrEnum
from typing import Any
from uuid import UUID


class UnknownTokenError(ValueError):
    pass


class TokenCategory(StrEnum):
    PERSON_NAME = "PERSON_NAME"
    PHONE = "PHONE"
    EMAIL = "EMAIL"
    EXTERNAL_ID = "EXTERNAL_ID"


PERSON_ENTITY_TYPES = frozenset({"teacher", "student"})
EXTERNAL_ID_FIELDS = frozenset(
    {
        "source_id",
        "employee_number",
        "student_number",
        "member_source_id",
        "container_source_id",
        "parent_source_id",
        "department_source_id",
        "class_source_id",
    }
)
TOKEN_PATTERN = re.compile(r"^(PERSON_NAME|PHONE|EMAIL|EXTERNAL_ID)_[A-F0-9]{12}$")
FIELD_VALUE_KEYS = frozenset(
    {"source_value", "target_value", "normalized_source", "normalized_target", "before", "after"}
)


class TaskTokenizationContext:
    def __init__(self, *, secret: str, tenant_id: str, task_id: UUID) -> None:
        if len(secret) < 16:
            raise ValueError("tokenization secret must contain at least 16 characters")
        self.secret = secret.encode("utf-8")
        self.tenant_id = tenant_id
        self.task_id = task_id
        self._reverse: dict[str, str] = {}

    def tokenize_value(
        self,
        field: str,
        value: str,
        *,
        entity_type: str | None = None,
    ) -> str:
        category = _category_for(field, entity_type)
        if category is None:
            return value
        normalized = unicodedata.normalize("NFKC", value).strip().casefold()
        material = f"{self.tenant_id}\x1f{self.task_id}\x1f{category.value}\x1f{normalized}"
        digest = hmac.new(self.secret, material.encode("utf-8"), hashlib.sha256).hexdigest()
        token = f"{category.value}_{digest[:12].upper()}"
        self._reverse[token] = value
        return token

    def tokenize(
        self,
        value: Any,
        *,
        field: str | None = None,
        entity_type: str | None = None,
    ) -> Any:
        if isinstance(value, dict):
            local_type = value.get("entity_type", entity_type)
            normalized_type = str(local_type) if local_type is not None else entity_type
            declared_field = value.get("field")
            return {
                key: self.tokenize(
                    item,
                    field=(
                        str(declared_field)
                        if key in FIELD_VALUE_KEYS and declared_field is not None
                        else key
                    ),
                    entity_type=normalized_type,
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self.tokenize(item, field=field, entity_type=entity_type) for item in value]
        if isinstance(value, tuple):
            return tuple(
                self.tokenize(item, field=field, entity_type=entity_type) for item in value
            )
        if isinstance(value, str) and field is not None:
            return self.tokenize_value(field, value, entity_type=entity_type)
        return value

    def detokenize(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: self.detokenize(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.detokenize(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.detokenize(item) for item in value)
        if isinstance(value, str):
            if value in self._reverse:
                return self._reverse[value]
            if TOKEN_PATTERN.fullmatch(value):
                raise UnknownTokenError(f"unknown model token: {value}")
        return value


def _category_for(field: str, entity_type: str | None) -> TokenCategory | None:
    normalized_field = field.casefold()
    if normalized_field == "phone":
        return TokenCategory.PHONE
    if normalized_field == "email":
        return TokenCategory.EMAIL
    if normalized_field == "name" and entity_type in PERSON_ENTITY_TYPES:
        return TokenCategory.PERSON_NAME
    if normalized_field in EXTERNAL_ID_FIELDS or normalized_field.endswith("_source_id"):
        return TokenCategory.EXTERNAL_ID
    return None
