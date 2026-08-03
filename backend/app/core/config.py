import json
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import (
    Field,
    NonNegativeFloat,
    PositiveFloat,
    PositiveInt,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.connectors.config_file import load_database_connector_configurations
from app.connectors.configured import (
    ApiConnectorConfiguration,
    DatabaseConnectorConfiguration,
)

DEFAULT_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


class LLMResponseMode(StrEnum):
    JSON_SCHEMA = "json_schema"
    JSON_OBJECT = "json_object"
    PROMPT_JSON = "prompt_json"


RESERVED_LLM_BODY_FIELDS = frozenset({"model", "messages", "response_format", "stream"})
RESERVED_EMBEDDING_BODY_FIELDS = frozenset({"model", "input"})
MAX_LLM_EXTRA_JSON_BYTES = 32 * 1024


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=DEFAULT_ENV_FILE,
        env_prefix="RECONCILIATION_",
        extra="ignore",
    )

    app_name: str = "Organization Reconciliation API"
    database_url: str = "postgresql+asyncpg://reconcile:reconcile@localhost:5432/reconcile"
    upload_root: Path = Path("storage/uploads")
    snapshot_root: Path = Path("storage/snapshots")
    quarantine_root: Path = Path("storage/quarantine")
    export_root: Path = Path("storage/exports")
    agent_local_read_roots: tuple[Path, ...] = ()
    agent_local_write_roots: tuple[Path, ...] = ()
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
    llm_timeout_seconds: PositiveFloat = 120
    conversation_context_max_tokens: PositiveInt = 65_536
    conversation_context_reserved_output_tokens: PositiveInt = 2_048
    tokenization_secret: SecretStr | None = None
    proposal_preview_secret: SecretStr | None = None
    analysis_batch_size: PositiveInt = Field(default=10, le=10)
    analysis_worker_lease_seconds: PositiveInt = 150
    analysis_worker_concurrency: PositiveInt = 4
    analysis_worker_poll_seconds: PositiveFloat = 0.5
    analysis_worker_retry_wait_seconds: NonNegativeFloat = 2
    embedding_url: str | None = None
    embedding_api_key: SecretStr | None = None
    embedding_model: str = "organization-embedding"
    embedding_auth_header: str = "Authorization"
    embedding_auth_scheme: str = "Bearer"
    embedding_extra_headers_json: dict[str, str] = {}
    embedding_extra_body_json: dict[str, Any] = {}
    embedding_timeout_seconds: PositiveFloat = 20
    embedding_dimensions: PositiveInt = 1536
    rematching_enabled: bool = False
    rematching_shadow_mode: bool = True
    rematching_top_k: PositiveInt = 3
    rematching_high_confidence_threshold: float = Field(default=0.9, ge=0, le=1)
    rematching_worker_lease_seconds: PositiveInt = 60
    rematching_worker_concurrency: PositiveInt = 4
    rematching_worker_retry_attempts: PositiveInt = 3
    rematching_worker_retry_wait_seconds: NonNegativeFloat = 2
    matching_quality_policy_version: str = "matching-quality-v1"
    matching_quality_min_population: PositiveInt = 10
    matching_quality_max_unresolved_ratio: float = Field(default=0.2, ge=0, le=1)
    matching_quality_max_create_ratio: float = Field(default=0.2, ge=0, le=1)
    matching_quality_max_disable_ratio: float = Field(default=0.2, ge=0, le=1)
    model_retry_attempts: PositiveInt = 3
    model_retry_wait_seconds: NonNegativeFloat = 0.2
    agent_privacy_policy_version: str = "student-phone-v1"
    new_agent_enabled: bool = False
    agent_graph_enabled: bool = False
    agent_graph_csv_execution_enabled: bool = False
    source_ingestion_v2_enabled: bool = False
    source_ingestion_v3_enabled: bool = False
    agent_graph_sql_execution_enabled: bool = False
    new_agent_analysis_only: bool = True
    new_agent_csv_execution_enabled: bool = False
    new_agent_api_connector_enabled: bool = False
    new_agent_database_connector_enabled: bool = False
    conversation_remote_csv_enabled: bool = False
    api_connector_secret_key: SecretStr | None = None
    api_connector_connect_timeout_seconds: PositiveFloat = 10
    api_connector_read_timeout_seconds: PositiveFloat = 30
    api_connector_test_max_age_seconds: PositiveInt = 24 * 60 * 60
    remote_source_max_redirects: int = Field(default=3, ge=0, le=5)
    remote_source_connect_timeout_seconds: PositiveFloat = 10
    remote_source_read_timeout_seconds: PositiveFloat = 30
    remote_source_total_timeout_seconds: PositiveFloat = 60
    api_connector_configurations: dict[str, ApiConnectorConfiguration] = {}
    database_connector_config_file: Path | None = None
    database_connector_configurations: dict[str, DatabaseConnectorConfiguration] = {}
    database_connector_credentials: dict[str, SecretStr] = {}

    @field_validator(
        "llm_auth_header",
        "llm_model",
        "embedding_auth_header",
        "embedding_model",
        "matching_quality_policy_version",
    )
    @classmethod
    def reject_blank_gateway_value(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped or any(character in stripped for character in "\r\n"):
            raise ValueError("LLM gateway setting must be a non-blank single line")
        return stripped

    @field_validator("agent_local_read_roots", "agent_local_write_roots")
    @classmethod
    def canonicalize_agent_local_roots(cls, values: tuple[Path, ...]) -> tuple[Path, ...]:
        return tuple(path.expanduser().resolve() for path in values)

    @field_validator("database_connector_config_file")
    @classmethod
    def resolve_database_connector_config_file(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        path = value.expanduser()
        if not path.is_absolute():
            path = DEFAULT_ENV_FILE.parent / path
        return path.resolve()

    @field_validator("llm_auth_scheme", "embedding_auth_scheme")
    @classmethod
    def normalize_auth_scheme(cls, value: str) -> str:
        stripped = value.strip()
        if any(character in stripped for character in "\r\n"):
            raise ValueError("LLM authentication scheme must be a single line")
        return stripped

    @model_validator(mode="after")
    def validate_gateway_extensions(self) -> "Settings":
        if self.database_connector_config_file is not None:
            file_configurations = load_database_connector_configurations(
                self.database_connector_config_file
            )
            duplicate_ids = set(file_configurations).intersection(
                self.database_connector_configurations
            )
            if duplicate_ids:
                raise ValueError(
                    "duplicate database connector configuration ID: "
                    f"{sorted(duplicate_ids)[0]}"
                )
            unresolved_credential_ids = sorted(
                connector_id
                for connector_id, configuration in file_configurations.items()
                if configuration.credential_reference
                not in self.database_connector_credentials
            )
            if unresolved_credential_ids:
                raise ValueError(
                    "database connector credential reference is unavailable: "
                    f"{unresolved_credential_ids[0]}"
                )
            self.database_connector_configurations = {
                **file_configurations,
                **self.database_connector_configurations,
            }
        if self.conversation_context_reserved_output_tokens >= self.conversation_context_max_tokens:
            raise ValueError(
                "conversation reserved output tokens must be smaller than context maximum"
            )
        for write_root in self.agent_local_write_roots:
            if not any(
                _path_is_within(write_root, read_root) for read_root in self.agent_local_read_roots
            ):
                raise ValueError(
                    "local write root must be contained by a configured local read root"
                )
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
        embedding_body_overlap = sorted(
            RESERVED_EMBEDDING_BODY_FIELDS.intersection(self.embedding_extra_body_json)
        )
        if embedding_body_overlap:
            raise ValueError(
                f"reserved embedding body field cannot be overridden: {embedding_body_overlap[0]}"
            )
        reserved_embedding_headers = {
            "content-type",
            self.embedding_auth_header.casefold(),
        }
        embedding_header_overlap = sorted(
            header
            for header in self.embedding_extra_headers_json
            if header.casefold() in reserved_embedding_headers
        )
        if embedding_header_overlap:
            raise ValueError(
                f"reserved embedding header cannot be overridden: {embedding_header_overlap[0]}"
            )
        for values in (
            self.embedding_extra_headers_json,
            self.embedding_extra_body_json,
        ):
            encoded = json.dumps(values, ensure_ascii=True, separators=(",", ":")).encode()
            if len(encoded) > MAX_LLM_EXTRA_JSON_BYTES:
                raise ValueError("embedding gateway extension JSON exceeds the size limit")
        child_flags = (
            self.new_agent_csv_execution_enabled,
            self.new_agent_api_connector_enabled,
            self.new_agent_database_connector_enabled,
        )
        if any(child_flags) and not self.new_agent_enabled:
            raise ValueError("new_agent_enabled is required for Agent rollout flags")
        if self.new_agent_analysis_only and any(child_flags):
            raise ValueError("new_agent_analysis_only cannot enable target execution")
        if self.new_agent_api_connector_enabled:
            if self.api_connector_secret_key is None:
                raise ValueError(
                    "API connector secret key is required before enabling execution"
                )
            from cryptography.fernet import Fernet

            try:
                Fernet(self.api_connector_secret_key.get_secret_value().encode())
            except (TypeError, ValueError) as error:
                raise ValueError("API connector secret key must be a valid Fernet key") from error
        if self.new_agent_database_connector_enabled and not self.database_connector_configurations:
            raise ValueError(
                "database connector configuration is required before enabling execution"
            )
        if self.agent_graph_enabled and not self.new_agent_enabled:
            raise ValueError("new_agent_enabled is required for agent_graph_enabled")
        if self.agent_graph_csv_execution_enabled and not self.agent_graph_enabled:
            raise ValueError(
                "agent_graph_enabled is required for agent_graph_csv_execution_enabled"
            )
        if self.source_ingestion_v2_enabled and not self.agent_graph_enabled:
            raise ValueError("agent_graph_enabled is required for source_ingestion_v2_enabled")
        if self.source_ingestion_v3_enabled and not self.agent_graph_enabled:
            raise ValueError("agent_graph_enabled is required for source_ingestion_v3_enabled")
        if self.conversation_remote_csv_enabled and not self.source_ingestion_v2_enabled:
            raise ValueError(
                "source_ingestion_v2_enabled is required for conversation remote CSV"
            )
        if self.agent_graph_sql_execution_enabled and not self.agent_graph_enabled:
            raise ValueError(
                "agent_graph_enabled is required for agent_graph_sql_execution_enabled"
            )
        if self.agent_graph_sql_execution_enabled and not (
            self.source_ingestion_v2_enabled or self.source_ingestion_v3_enabled
        ):
            raise ValueError(
                "versioned source ingestion is required for SQL graph execution"
            )
        if self.agent_graph_sql_execution_enabled:
            self._validate_sql_graph_connectors()
        if self.new_agent_analysis_only and self.agent_graph_csv_execution_enabled:
            raise ValueError("new_agent_analysis_only cannot enable Agent graph target execution")
        if self.new_agent_analysis_only and self.agent_graph_sql_execution_enabled:
            raise ValueError("new_agent_analysis_only cannot enable Agent graph target execution")
        return self

    def _validate_sql_graph_connectors(self) -> None:
        if not self.database_connector_configurations:
            raise ValueError("database connector configuration is required for SQL graph execution")
        fixed_fields = {
            "category",
            "name",
            "number",
            "class_name",
            "phone",
            "email",
        }
        authoritative_count = 0
        target_count = 0
        for configuration in self.database_connector_configurations.values():
            missing_fields = fixed_fields.difference(configuration.field_columns)
            unknown_fields = set(configuration.field_columns).difference(fixed_fields)
            if unknown_fields:
                raise ValueError("SQL graph connector contains unsupported organization fields")
            if configuration.credential_reference not in self.database_connector_credentials:
                raise ValueError("SQL graph connector credential reference is unavailable")
            capabilities = configuration.capabilities
            if not capabilities.read or not capabilities.paginated:
                raise ValueError("SQL graph connector requires stable paginated reads")
            if configuration.source_role == "authoritative":
                authoritative_count += 1
                if capabilities.create or capabilities.update or capabilities.delete:
                    raise ValueError("authoritative SQL graph connector must be read-only")
                continue
            target_count += 1
            if configuration.mapping.mode == "explicit" and missing_fields:
                raise ValueError("SQL graph target is missing fixed organization fields")
            if configuration.dialect != "mysql":
                raise ValueError("SQL graph writable target must use MySQL")
            if not (
                capabilities.create
                and capabilities.update
                and capabilities.delete
                and capabilities.optimistic_version
                and capabilities.read_after_write
            ):
                raise ValueError(
                    "SQL graph target requires create, update, delete, "
                    "optimistic version, and read-after-write capabilities"
                )
        if target_count == 0 or (
            authoritative_count == 0 and not self.source_ingestion_v3_enabled
        ):
            raise ValueError("SQL graph execution requires authoritative and target connectors")

    @property
    def model_gateway_configured(self) -> bool:
        api_key = self.llm_api_key.get_secret_value() if self.llm_api_key is not None else ""
        token_secret = (
            self.tokenization_secret.get_secret_value()
            if self.tokenization_secret is not None
            else ""
        )
        return bool(self.llm_url and api_key and self.llm_model and token_secret)

    @property
    def new_task_workflow_version(self) -> str:
        if self.new_agent_enabled and self.agent_graph_enabled:
            return "agent-graph-v1"
        return "new-agent-v1" if self.new_agent_enabled else "legacy-v1"

    def validate_agent_worker_configuration(self) -> None:
        if not self.new_agent_enabled:
            raise ValueError("new Agent workflow flag is disabled")
        if not self.model_gateway_configured:
            raise ValueError("Agent model gateway and tokenization must be configured")
        if self.analysis_batch_size > 10:
            raise ValueError("Agent analysis batch maximum is 10")
        if self.model_retry_attempts != 3:
            raise ValueError("Agent model retry count must be exactly three")
        if self.analysis_worker_lease_seconds <= self.llm_timeout_seconds:
            raise ValueError("Agent worker lease must exceed the model request timeout")
        if self.agent_privacy_policy_version != "student-phone-v1":
            raise ValueError("unsupported Agent privacy policy version")

    def ensure_storage_directories(self) -> None:
        for directory in (
            self.upload_root,
            self.snapshot_root,
            self.quarantine_root,
            self.export_root,
        ):
            directory.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
