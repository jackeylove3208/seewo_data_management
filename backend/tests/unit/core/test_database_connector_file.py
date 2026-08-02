from pathlib import Path

from app.connectors.config_file import load_database_connector_configurations
from app.core.config import Settings

CONFIG_FILE = Path(__file__).resolve().parents[3] / "config" / "database-connectors.yaml"


def test_database_connector_yaml_preserves_seewo_and_adds_llm_data_target() -> None:
    connectors = load_database_connector_configurations(CONFIG_FILE)

    assert {
        "authority-mysql",
        "third-data-mysql",
        "seewo-mysql",
        "seewo-data-mysql",
    } <= set(connectors)

    third_data = connectors["third-data-mysql"]
    assert third_data.credential_reference == "secret://connectors/authority-mysql"
    assert third_data.database_name is None
    assert third_data.schema_name == "third_data"
    assert third_data.table_name == "third_data"
    assert third_data.primary_key == "row_id"
    assert third_data.version_column == "version"
    assert third_data.source_role == "authoritative"
    assert third_data.mapping.mode == "llm"
    assert third_data.field_columns == {}
    assert third_data.allowed_columns == ()
    assert third_data.capabilities.read is True
    assert third_data.capabilities.paginated is True
    assert third_data.capabilities.create is False
    assert third_data.capabilities.update is False
    assert third_data.capabilities.delete is False

    seewo = connectors["seewo-mysql"]
    assert seewo.source_role == "target"
    assert seewo.database_name == "seewo_db"
    assert seewo.table_name == "organization_people"
    assert seewo.mapping.mode == "explicit"
    assert seewo.capabilities.create is True
    assert seewo.capabilities.update is True
    assert seewo.capabilities.delete is True

    seewo_data = connectors["seewo-data-mysql"]
    assert seewo_data.credential_reference == "secret://connectors/seewo-data-mysql"
    assert seewo_data.database_name == "seewo_data"
    assert seewo_data.table_name == "data"
    assert seewo_data.primary_key == "row_id"
    assert seewo_data.version_column == "version"
    assert seewo_data.source_role == "target"
    assert seewo_data.mapping.mode == "llm"
    assert seewo_data.field_columns == {}
    assert seewo_data.allowed_columns == ()
    assert seewo_data.capabilities.read is True
    assert seewo_data.capabilities.paginated is True
    assert seewo_data.capabilities.create is True
    assert seewo_data.capabilities.update is True
    assert seewo_data.capabilities.delete is True
    assert seewo_data.capabilities.optimistic_version is True
    assert seewo_data.capabilities.read_after_write is True


def test_database_connector_yaml_coexists_with_empty_legacy_environment_json() -> None:
    settings = Settings(
        database_connector_config_file=CONFIG_FILE,
        database_connector_configurations={},
        database_connector_credentials={
            connector.credential_reference: "mysql+asyncmy://hidden"
            for connector in load_database_connector_configurations(CONFIG_FILE).values()
        },
        _env_file=None,
    )

    assert set(settings.database_connector_configurations) == {
        "authority-mysql",
        "third-data-mysql",
        "seewo-mysql",
        "seewo-data-mysql",
    }
