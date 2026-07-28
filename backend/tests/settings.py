from typing import Any

from app.core.config import Settings


def build_test_settings(**overrides: Any) -> Settings:
    overrides.setdefault("_env_file", None)
    return Settings(**overrides)
