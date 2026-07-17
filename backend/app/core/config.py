from functools import lru_cache
from pathlib import Path

from pydantic import NonNegativeFloat, PositiveFloat, PositiveInt, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    llm_timeout_seconds: PositiveFloat = 20
    embedding_url: str | None = None
    embedding_api_key: SecretStr | None = None
    embedding_model: str = "organization-embedding"
    embedding_timeout_seconds: PositiveFloat = 20
    embedding_dimensions: PositiveInt = 1536
    model_retry_attempts: PositiveInt = 3
    model_retry_wait_seconds: NonNegativeFloat = 0.2

    def ensure_storage_directories(self) -> None:
        for directory in (self.upload_root, self.snapshot_root, self.quarantine_root):
            directory.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
