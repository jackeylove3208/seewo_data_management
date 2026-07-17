import json
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import (
    NonNegativeFloat,
    PositiveFloat,
    PositiveInt,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMResponseMode(StrEnum):
    JSON_SCHEMA = "json_schema"
    JSON_OBJECT = "json_object"
    PROMPT_JSON = "prompt_json"


RESERVED_LLM_BODY_FIELDS = frozenset({"model", "messages", "response_format", "stream"})
MAX_LLM_EXTRA_JSON_BYTES = 32 * 1024


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="RECONCILIATION_",
        extra="ignore",
    )

    app_name: str = "Organization Reconciliation API"
    database_url: str = "postgresql+asyncpg://reconcile:reconcile@localhost:5432/reconcile"
    upload_root: Path = Path("storage/uploads")
    snapshot_root: Path = Path("storage/snapshots")
    quarantine_root: Path = Path("storage/quarantine")
    max_upload_bytes: PositiveInt = 50 * 1024 * 1024
    demo_operator_id: str = "demo-operator"
    demo_tenant_id: str = "school-1"
    auto_create_schema: bool = False
    llm_url: str | None = None
    llm_api_key: SecretStr | None = None
    llm_model: str = "governance-analysis"
    llm_auth_header: str = "Authorization"
    llm_auth_scheme: str = "Bearer"
    llm_response_mode: LLMResponseMode = LLMResponseMode.JSON_SCHEMA
    llm_extra_headers_json: dict[str, str] = {}
    llm_extra_body_json: dict[str, Any] = {}
    llm_timeout_seconds: PositiveFloat = 20
    tokenization_secret: SecretStr | None = None
    analysis_batch_size: PositiveInt = 10
    embedding_url: str | None = None
    embedding_api_key: SecretStr | None = None
    embedding_model: str = "organization-embedding"
    embedding_timeout_seconds: PositiveFloat = 20
    embedding_dimensions: PositiveInt = 1536
    model_retry_attempts: PositiveInt = 3
    model_retry_wait_seconds: NonNegativeFloat = 0.2

    @field_validator("llm_auth_header", "llm_model")
    @classmethod
    def reject_blank_gateway_value(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped or any(character in stripped for character in "\r\n"):
            raise ValueError("LLM gateway setting must be a non-blank single line")
        return stripped

    @field_validator("llm_auth_scheme")
    @classmethod
    def normalize_auth_scheme(cls, value: str) -> str:
        stripped = value.strip()
        if any(character in stripped for character in "\r\n"):
            raise ValueError("LLM authentication scheme must be a single line")
        return stripped

    @model_validator(mode="after")
    def validate_gateway_extensions(self) -> "Settings":
        body_overlap = sorted(RESERVED_LLM_BODY_FIELDS.intersection(self.llm_extra_body_json))
        if body_overlap:
            raise ValueError(f"reserved LLM body field cannot be overridden: {body_overlap[0]}")
        reserved_headers = {"content-type", self.llm_auth_header.casefold()}
        header_overlap = sorted(
            header
            for header in self.llm_extra_headers_json
            if header.casefold() in reserved_headers
        )
        if header_overlap:
            raise ValueError(f"reserved LLM header cannot be overridden: {header_overlap[0]}")
        for values in (self.llm_extra_headers_json, self.llm_extra_body_json):
            encoded = json.dumps(values, ensure_ascii=True, separators=(",", ":")).encode()
            if len(encoded) > MAX_LLM_EXTRA_JSON_BYTES:
                raise ValueError("LLM gateway extension JSON exceeds the size limit")
        return self

    @property
    def model_gateway_configured(self) -> bool:
        api_key = self.llm_api_key.get_secret_value() if self.llm_api_key is not None else ""
        token_secret = (
            self.tokenization_secret.get_secret_value()
            if self.tokenization_secret is not None
            else ""
        )
        return bool(self.llm_url and api_key and self.llm_model and token_secret)

    def ensure_storage_directories(self) -> None:
        for directory in (self.upload_root, self.snapshot_root, self.quarantine_root):
            directory.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
