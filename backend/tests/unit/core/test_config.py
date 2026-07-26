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
    assert settings.agent_graph_enabled is False
    assert settings.agent_graph_csv_execution_enabled is False
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


def test_enabling_agent_graph_routes_only_new_tasks_to_graph_workflow() -> None:
    settings = Settings(
        new_agent_enabled=True,
        agent_graph_enabled=True,
        _env_file=None,
    )

    assert settings.new_task_workflow_version == "agent-graph-v1"
    assert settings.agent_graph_csv_execution_enabled is False


def test_agent_graph_execution_flags_fail_closed_without_graph_runtime() -> None:
    with pytest.raises(ValueError, match="agent_graph_enabled"):
        Settings(
            new_agent_enabled=True,
            new_agent_analysis_only=False,
            agent_graph_csv_execution_enabled=True,
            _env_file=None,
        )


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


def test_agent_model_timeout_allows_structured_analysis_to_finish() -> None:
    assert Settings(_env_file=None).llm_timeout_seconds == 60
    assert Settings(_env_file=None).analysis_worker_lease_seconds == 90


def test_agent_worker_configuration_requires_gateway_retry_and_privacy_contract() -> None:
    settings = Settings(
        new_agent_enabled=True,
        llm_url="https://gateway.example.test/v1/chat/completions",
        llm_api_key="test-key",
        tokenization_secret="long-tokenization-secret",
        model_retry_attempts=3,
        agent_privacy_policy_version="student-phone-v1",
        _env_file=None,
    )

    settings.validate_agent_worker_configuration()

    with pytest.raises(ValueError, match="retry"):
        settings.model_copy(
            update={"model_retry_attempts": 2}
        ).validate_agent_worker_configuration()
    with pytest.raises(ValueError, match="privacy"):
        settings.model_copy(
            update={"agent_privacy_policy_version": "unknown"}
        ).validate_agent_worker_configuration()
    with pytest.raises(ValueError, match="gateway"):
        settings.model_copy(update={"llm_api_key": None}).validate_agent_worker_configuration()
    with pytest.raises(ValueError, match="lease"):
        settings.model_copy(
            update={
                "analysis_worker_lease_seconds": 60,
                "llm_timeout_seconds": 60,
            }
        ).validate_agent_worker_configuration()


def test_agent_local_write_roots_are_canonical_and_nested_under_read_roots(
    tmp_path: Path,
) -> None:
    read_root = tmp_path / "sources"
    write_root = read_root / "seewo"

    settings = Settings(
        agent_local_read_roots=(read_root,),
        agent_local_write_roots=(write_root,),
        _env_file=None,
    )

    assert settings.agent_local_read_roots == (read_root.resolve(),)
    assert settings.agent_local_write_roots == (write_root.resolve(),)


def test_agent_local_write_root_outside_read_roots_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="local write root must be contained"):
        Settings(
            agent_local_read_roots=(tmp_path / "read",),
            agent_local_write_roots=(tmp_path / "write",),
            _env_file=None,
        )
