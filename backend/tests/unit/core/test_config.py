from pathlib import Path

import pytest

from app.core.config import DEFAULT_ENV_FILE, Settings


def test_default_env_file_is_backend_absolute_path() -> None:
    assert DEFAULT_ENV_FILE == Path(__file__).resolve().parents[3] / ".env"
    assert DEFAULT_ENV_FILE.is_absolute()
    assert Settings.model_config["env_file"] == DEFAULT_ENV_FILE


def test_new_agent_rollout_is_safe_by_default() -> None:
    settings = Settings(_env_file=None)

    assert settings.new_agent_enabled is False
    assert settings.new_agent_analysis_only is True
    assert settings.new_agent_csv_execution_enabled is False
    assert settings.new_agent_api_connector_enabled is False
    assert settings.new_agent_database_connector_enabled is False
    assert settings.new_task_workflow_version == "legacy-v1"


def test_enabling_agent_selects_new_workflow_without_enabling_execution() -> None:
    settings = Settings(new_agent_enabled=True, _env_file=None)

    assert settings.new_task_workflow_version == "new-agent-v1"
    assert settings.new_agent_analysis_only is True
    assert settings.new_agent_csv_execution_enabled is False


def test_agent_execution_flags_fail_closed_without_runtime() -> None:
    with pytest.raises(ValueError, match="new_agent_enabled"):
        Settings(new_agent_csv_execution_enabled=True, _env_file=None)


def test_analysis_only_mode_rejects_target_execution() -> None:
    with pytest.raises(ValueError, match="analysis_only"):
        Settings(
            new_agent_enabled=True,
            new_agent_analysis_only=True,
            new_agent_csv_execution_enabled=True,
            _env_file=None,
        )


def test_connector_execution_flags_require_server_side_connector_configuration() -> None:
    with pytest.raises(ValueError, match="API connector configuration"):
        Settings(
            new_agent_enabled=True,
            new_agent_analysis_only=False,
            new_agent_api_connector_enabled=True,
            _env_file=None,
        )

    configured = Settings(
        new_agent_enabled=True,
        new_agent_analysis_only=False,
        new_agent_api_connector_enabled=True,
        api_connector_configurations={
            "seewo": {
                "credential_reference": "secret://connectors/seewo-api",
                "endpoint": "https://connector.example.test/v1/people",
                "record_id_field": "id",
                "version_field": "etag",
            }
        },
        _env_file=None,
    )

    assert configured.api_connector_configurations["seewo"].credential_reference.endswith(
        "seewo-api"
    )


def test_agent_batch_size_cannot_exceed_connector_contract_limit() -> None:
    with pytest.raises(ValueError, match="less than or equal to 50"):
        Settings(analysis_batch_size=51, _env_file=None)
