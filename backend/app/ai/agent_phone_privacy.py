"""Task-scoped model-boundary protection for student telephone numbers only."""

import hashlib
import hmac
import re
from uuid import UUID


class UnknownStudentPhoneToken(ValueError):
    pass


_TOKEN = re.compile(r"^STUDENT_PHONE_[A-F0-9]{12}$")


class StudentPhoneTokenizationContext:
    def __init__(self, *, secret: str, tenant_id: str, task_id: UUID) -> None:
        if len(secret) < 16:
            raise ValueError(
                "student phone tokenization secret must contain at least 16 characters"
            )
        self._secret = secret.encode()
        self._tenant_id = tenant_id
        self._task_id = task_id
        self._issued: set[str] = set()
        self._reverse: dict[str, str] = {}

    def tokenize(self, value: str | None, *, entity_kind: str) -> str | None:
        if value is None or entity_kind != "student":
            return value
        material = f"{self._tenant_id}\x1f{self._task_id}\x1f{value}".encode()
        token = "STUDENT_PHONE_" + hmac.new(
            self._secret, material, hashlib.sha256
        ).hexdigest()[:12].upper()
        self._issued.add(token)
        self._reverse[token] = value
        return token

    def assert_known_tokens(self, values: set[str]) -> None:
        unknown = {
            value for value in values if _TOKEN.fullmatch(value) and value not in self._issued
        }
        if unknown:
            raise UnknownStudentPhoneToken("model response contains an unknown student-phone token")

    def detokenize(self, value: str | None) -> str | None:
        if value is None:
            return None
        if not _TOKEN.fullmatch(value):
            raise UnknownStudentPhoneToken(
                "model response contains a non-tokenized student phone"
            )
        try:
            return self._reverse[value]
        except KeyError as error:
            raise UnknownStudentPhoneToken(
                "model response contains an unknown student-phone token"
            ) from error
