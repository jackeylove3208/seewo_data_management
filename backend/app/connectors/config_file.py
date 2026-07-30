"""Strict loader for database connector configuration files."""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from app.connectors.configured import DatabaseConnectorConfiguration


class DatabaseConnectorConfigurationFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connectors: dict[str, DatabaseConnectorConfiguration] = Field(default_factory=dict)


def load_database_connector_configurations(
    path: Path,
) -> dict[str, DatabaseConnectorConfiguration]:
    """Load validated database connector configurations from a YAML file."""
    try:
        contents: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ValueError(
            f"database connector configuration YAML could not be loaded: {path}"
        ) from error
    if not isinstance(contents, dict):
        raise ValueError("database connector configuration YAML root must be an object")
    return DatabaseConnectorConfigurationFile.model_validate(contents).connectors
