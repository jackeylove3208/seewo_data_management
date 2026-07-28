from pathlib import Path

import pytest
from pydantic import SecretStr

from app.core.config import DEFAULT_ENV_FILE, Settings
from tests.settings import build_test_settings


def test_default_env_file_is_backend_absolute_path() -> None:
    assert DEFAULT_ENV_FILE == Path(__file__).resolve().parents[3] / ".env"
    assert DEFAULT_ENV_FILE.is_absolute()
    assert Settings.model_config["env_file"] == DEFAULT_ENV_FILE


def test_build_test_settings_ignores_project_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            (
                "RECONCILIATION_NEW_AGENT_ENABLED=true",
                "RECONCILIATION_AGENT_GRAPH_ENABLED=true",
                "RECONCILIATION_AGENT_GRAPH_CSV_EXECUTION_ENABLED=true",
            )
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="target execution"):
        Settings(new_agent_enabled=True, new_agent_analysis_only=True, _env_file=env_file)

    settings = build_test_settings(new_agent_enabled=True, new_agent_analysis_only=True)

    assert settings.new_agent_analysis_only is True
    assert settings.agent_graph_csv_execution_enabled is False


def test_new_agent_rollout_is_safe_by_default() -> None:
    settings = Settings(_env_file=None)

    assert settings.new_agent_enabled is False
    assert settings.agent_graph_enabled is False
    assert settings.agent_graph_csv_execution_enabled is False
    assert settings.source_ingestion_v2_enabled is False
    assert settings.agent_graph_sql_execution_enabled is False
    assert settings.conversation_remote_csv_enabled is False
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

    with pytest.raises(ValueError, match="agent_graph_enabled"):
        Settings(
            new_agent_enabled=True,
            new_agent_analysis_only=False,
            agent_graph_sql_execution_enabled=True,
            _env_file=None,
        )


def test_source_ingestion_v2_requires_agent_graph_runtime() -> None:
    with pytest.raises(ValueError, match="agent_graph_enabled"):
        Settings(
            new_agent_enabled=True,
            source_ingestion_v2_enabled=True,
            _env_file=None,
        )


def test_conversation_remote_csv_requires_versioned_graph_ingestion() -> None:
    with pytest.raises(ValueError, match="source_ingestion_v2_enabled"):
        Settings(
            new_agent_enabled=True,
            agent_graph_enabled=True,
            conversation_remote_csv_enabled=True,
            _env_file=None,
        )

    settings = Settings(
        new_agent_enabled=True,
        agent_graph_enabled=True,
        source_ingestion_v2_enabled=True,
        conversation_remote_csv_enabled=True,
        _env_file=None,
    )
    assert settings.conversation_remote_csv_enabled is True


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


def test_sql_graph_accepts_read_only_postgresql_source_and_writable_mysql_target() -> None:
    settings = Settings(
        new_agent_enabled=True,
        agent_graph_enabled=True,
        source_ingestion_v2_enabled=True,
        agent_graph_sql_execution_enabled=True,
        new_agent_analysis_only=False,
        database_connector_configurations={
            "authority-postgres": {
                "credential_reference": "secret://connectors/authority-postgres",
                "dialect": "postgresql",
                "table_name": "organization_people",
                "primary_key": "id",
                "version_column": "row_version",
                "field_columns": {
                    "category": "category",
                    "name": "name",
                    "number": "number",
                    "class_name": "class_name",
                    "phone": "phone",
                    "email": "email",
                },
                "source_role": "authoritative",
                "capabilities": {"read": True, "paginated": True},
            },
            "seewo-mysql": {
                "credential_reference": "secret://connectors/seewo-mysql",
                "dialect": "mysql",
                "table_name": "organization_people",
                "primary_key": "id",
                "version_column": "row_version",
                "field_columns": {
                    "category": "category",
                    "name": "name",
                    "number": "number",
                    "class_name": "class_name",
                    "phone": "phone",
                    "email": "email",
                },
                "source_role": "target",
                "capabilities": {
                    "read": True,
                    "paginated": True,
                    "create": True,
                    "update": True,
                    "delete": True,
                    "optimistic_version": True,
                    "read_after_write": True,
                },
            },
        },
        database_connector_credentials={
            "secret://connectors/authority-postgres": "postgresql+asyncpg://hidden",
            "secret://connectors/seewo-mysql": "mysql+asyncmy://hidden",
        },
        _env_file=None,
    )

    assert settings.database_connector_configurations["authority-postgres"].dialect == (
        "postgresql"
    )
    assert settings.database_connector_configurations["seewo-mysql"].dialect == "mysql"
    secret = settings.database_connector_credentials["secret://connectors/seewo-mysql"]
    assert isinstance(secret, SecretStr)
    assert "hidden" not in repr(settings)


def test_sql_graph_allows_authority_mapping_to_be_resolved_but_requires_target_mapping() -> None:
    common = {
        "new_agent_enabled": True,
        "agent_graph_enabled": True,
        "source_ingestion_v2_enabled": True,
        "agent_graph_sql_execution_enabled": True,
        "new_agent_analysis_only": False,
        "database_connector_credentials": {
            "secret://connectors/authority-postgres": ("postgresql+asyncpg://hidden"),
            "secret://connectors/seewo-mysql": "mysql+asyncmy://hidden",
        },
        "_env_file": None,
    }
    authority = {
        "credential_reference": "secret://connectors/authority-postgres",
        "dialect": "postgresql",
        "table_name": "organization_people",
        "primary_key": "id",
        "version_column": "row_version",
        "field_columns": {},
        "allowed_columns": [
            "id",
            "row_version",
            "entity_type",
            "full_name",
            "person_code",
            "class_label",
            "mobile",
            "mail",
        ],
        "source_role": "authoritative",
        "capabilities": {"read": True, "paginated": True},
    }
    target = {
        "credential_reference": "secret://connectors/seewo-mysql",
        "dialect": "mysql",
        "table_name": "organization_people",
        "primary_key": "id",
        "version_column": "row_version",
        "field_columns": {
            "category": "category",
            "name": "name",
            "number": "number",
            "class_name": "class_name",
            "phone": "phone",
            "email": "email",
        },
        "source_role": "target",
        "capabilities": {
            "read": True,
            "paginated": True,
            "create": True,
            "update": True,
            "delete": True,
            "optimistic_version": True,
            "read_after_write": True,
        },
    }

    settings = Settings(
        **common,
        database_connector_configurations={
            "authority-postgres": authority,
            "seewo-mysql": target,
        },
    )
    assert settings.database_connector_configurations["authority-postgres"].field_columns == {}

    with pytest.raises(
        ValueError,
        match="target.*missing fixed organization fields",
    ):
        Settings(
            **common,
            database_connector_configurations={
                "authority-postgres": authority,
                "seewo-mysql": {**target, "field_columns": {}},
            },
        )


def test_agent_batch_size_cannot_exceed_connector_contract_limit() -> None:
    with pytest.raises(ValueError, match="less than or equal to 50"):
        Settings(analysis_batch_size=51, _env_file=None)


def test_agent_model_timeout_allows_structured_analysis_to_finish() -> None:
    assert Settings(_env_file=None).llm_timeout_seconds == 60
    assert Settings(_env_file=None).analysis_worker_lease_seconds == 90


def test_conversation_context_budget_reserves_model_output_capacity() -> None:
    settings = Settings(_env_file=None)

    assert settings.conversation_context_max_tokens == 65_536
    assert settings.conversation_context_reserved_output_tokens == 2_048

    with pytest.raises(ValueError, match="reserved output"):
        Settings(
            conversation_context_max_tokens=2_048,
            conversation_context_reserved_output_tokens=2_048,
            _env_file=None,
        )


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
