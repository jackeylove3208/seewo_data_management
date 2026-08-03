import json
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from app.core.config import DEFAULT_ENV_FILE, Settings
from tests.settings import build_test_settings


def test_settings_load_llm_database_connector_yaml(tmp_path: Path) -> None:
    config = tmp_path / "database-connectors.yaml"
    config.write_text(
        """
connectors:
  seewo-data-mysql:
    credential_reference: secret://connectors/seewo-data-mysql
    dialect: mysql
    database_name: seewo_data
    table_name: data
    primary_key: row_id
    version_column: version
    source_role: target
    mapping:
      mode: llm
    capabilities:
      read: true
      paginated: true
      create: true
      update: true
      delete: true
      optimistic_version: true
      read_after_write: true
""",
        encoding="utf-8",
    )

    settings = Settings(
        database_connector_config_file=config,
        database_connector_credentials={
            "secret://connectors/seewo-data-mysql": "mysql+asyncmy://hidden"
        },
        agent_graph_enabled=True,
        source_ingestion_v3_enabled=True,
        agent_graph_sql_execution_enabled=True,
        new_agent_enabled=True,
        new_agent_analysis_only=False,
        _env_file=None,
    )

    connector = settings.database_connector_configurations["seewo-data-mysql"]
    assert connector.mapping.mode == "llm"
    assert connector.field_columns == {}
    assert connector.allowed_columns == ()


def test_settings_loads_llm_database_connector_yaml_with_empty_allowed_columns(
    tmp_path: Path,
) -> None:
    config = tmp_path / "database-connectors.yaml"
    config.write_text(
        """
connectors:
  seewo-data-mysql:
    credential_reference: secret://connectors/seewo-data-mysql
    dialect: mysql
    table_name: data
    primary_key: row_id
    version_column: version
    mapping:
      mode: llm
    allowed_columns: []
""",
        encoding="utf-8",
    )

    settings = Settings(
        database_connector_config_file=config,
        database_connector_credentials={
            "secret://connectors/seewo-data-mysql": "mysql+asyncmy://hidden"
        },
        _env_file=None,
    )

    assert settings.database_connector_configurations["seewo-data-mysql"].allowed_columns == ()


def test_settings_rejects_yaml_connector_with_unresolved_credential_reference(
    tmp_path: Path,
) -> None:
    config = tmp_path / "database-connectors.yaml"
    config.write_text(
        """
connectors:
  seewo-data-mysql:
    credential_reference: secret://connectors/seewo-data-mysql
    dialect: mysql
    table_name: data
    primary_key: row_id
    version_column: version
    mapping:
      mode: llm
""",
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="database connector credential reference is unavailable",
    ):
        Settings(database_connector_config_file=config, _env_file=None)


def test_settings_rejects_explicit_mapping_without_required_allowed_columns() -> None:
    with pytest.raises(ValueError, match="mapping exceeds its readable column allow-list"):
        Settings(
            database_connector_configurations={
                "seewo-data-mysql": {
                    "credential_reference": "secret://connectors/seewo-data-mysql",
                    "dialect": "mysql",
                    "table_name": "data",
                    "primary_key": "row_id",
                    "version_column": "version",
                    "field_columns": {"name": "name"},
                    "allowed_columns": ["row_id", "version"],
                }
            },
            _env_file=None,
        )


def test_settings_rejects_invalid_database_connector_yaml(tmp_path: Path) -> None:
    config = tmp_path / "database-connectors.yaml"
    config.write_text("connectors: [", encoding="utf-8")

    with pytest.raises(ValueError, match="database connector configuration YAML"):
        Settings(database_connector_config_file=config, _env_file=None)


def test_settings_rejects_unknown_database_connector_yaml_keys(tmp_path: Path) -> None:
    config = tmp_path / "database-connectors.yaml"
    config.write_text(
        """
connectors: {}
unexpected: true
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        Settings(database_connector_config_file=config, _env_file=None)


def test_settings_rejects_duplicate_database_connector_ids_from_yaml_and_environment(
    tmp_path: Path,
) -> None:
    config = tmp_path / "database-connectors.yaml"
    config.write_text(
        """
connectors:
  seewo-data-mysql:
    credential_reference: secret://connectors/seewo-data-mysql
    dialect: mysql
    table_name: data
    primary_key: row_id
    version_column: version
    field_columns:
      name: name
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate database connector configuration ID"):
        Settings(
            database_connector_config_file=config,
            database_connector_configurations={
                "seewo-data-mysql": {
                    "credential_reference": "secret://connectors/override",
                    "dialect": "mysql",
                    "table_name": "data",
                    "primary_key": "row_id",
                    "version_column": "version",
                    "field_columns": {"name": "name"},
                }
            },
            _env_file=None,
        )


def test_settings_loads_legacy_database_connector_environment_json(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "RECONCILIATION_DATABASE_CONNECTOR_CONFIGURATIONS="
        + json.dumps(
            {
                "seewo-data-mysql": {
                    "credential_reference": "secret://connectors/seewo-data-mysql",
                    "dialect": "mysql",
                    "table_name": "data",
                    "primary_key": "row_id",
                    "version_column": "version",
                    "field_columns": {"name": "name"},
                }
            }
        ),
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    connector = settings.database_connector_configurations["seewo-data-mysql"]
    assert connector.mapping.mode == "explicit"


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


def test_source_ingestion_v3_requires_agent_graph_runtime() -> None:
    with pytest.raises(ValueError, match="agent_graph_enabled"):
        Settings(
            new_agent_enabled=True,
            source_ingestion_v3_enabled=True,
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


def test_api_connector_execution_uses_dynamic_connection_store() -> None:
    configured = Settings(
        new_agent_enabled=True,
        new_agent_analysis_only=False,
        new_agent_api_connector_enabled=True,
        api_connector_secret_key=Fernet.generate_key().decode(),
        _env_file=None,
    )

    assert configured.api_connector_configurations == {}


def test_api_connector_execution_requires_valid_encryption_key() -> None:
    configuration = {
        "seewo": {
            "credential_reference": "secret://connectors/seewo-api",
            "endpoint": "https://connector.example.test/v1/people",
            "record_id_field": "id",
            "version_field": "etag",
        }
    }
    with pytest.raises(ValueError, match="secret key"):
        Settings(
            new_agent_enabled=True,
            new_agent_analysis_only=False,
            new_agent_api_connector_enabled=True,
            api_connector_configurations=configuration,
            _env_file=None,
        )
    with pytest.raises(ValueError, match="Fernet"):
        Settings(
            new_agent_enabled=True,
            new_agent_analysis_only=False,
            new_agent_api_connector_enabled=True,
            api_connector_configurations=configuration,
            api_connector_secret_key="not-a-fernet-key",
            _env_file=None,
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


def test_ingestion_v3_sql_graph_accepts_api_authority_and_only_mysql_target() -> None:
    settings = Settings(
        new_agent_enabled=True,
        new_agent_analysis_only=False,
        new_agent_api_connector_enabled=True,
        api_connector_secret_key=Fernet.generate_key().decode(),
        agent_graph_enabled=True,
        source_ingestion_v3_enabled=True,
        agent_graph_sql_execution_enabled=True,
        database_connector_configurations={
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
            }
        },
        database_connector_credentials={
            "secret://connectors/seewo-mysql": "mysql+asyncmy://hidden"
        },
        _env_file=None,
    )

    assert settings.source_ingestion_v3_enabled is True
    assert tuple(settings.database_connector_configurations) == ("seewo-mysql",)


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


def test_agent_batch_size_cannot_exceed_model_analysis_limit() -> None:
    settings = Settings(_env_file=None)

    assert settings.analysis_batch_size == 10
    assert settings.llm_max_output_tokens == 8_192
    assert Settings(analysis_batch_size=10, _env_file=None).analysis_batch_size == 10

    with pytest.raises(ValueError, match="less than or equal to 10"):
        Settings(analysis_batch_size=11, _env_file=None)

    with pytest.raises(ValueError, match="greater than 0"):
        Settings(llm_max_output_tokens=0, _env_file=None)


def test_agent_model_timeout_allows_structured_analysis_to_finish() -> None:
    assert Settings(_env_file=None).llm_timeout_seconds == 120
    assert Settings(_env_file=None).analysis_worker_lease_seconds == 150


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
