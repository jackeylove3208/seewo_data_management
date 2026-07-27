"""Deterministic SQL-to-Agent fixed contract projection."""

from uuid import UUID

from app.connectors.configured import (
    ConfiguredApiConnector,
    DatabaseConnectorConfiguration,
)
from app.ingestion.agent_contract import AgentContractMapper
from app.ingestion.agent_csv_adapter import AgentIngestionOutcome
from app.schemas.agent_ingestion import (
    AgentContractRecord,
    AgentEntityKind,
    AgentInputMark,
    AgentSourceRole,
)


class AgentDatabaseIngestionAdapter:
    """Read bounded connector pages and project configured columns to six fields."""

    def __init__(self, mapper: AgentContractMapper | None = None) -> None:
        self._mapper = mapper or AgentContractMapper()

    async def extract(
        self,
        *,
        connector: ConfiguredApiConnector,
        connector_id: str,
        task_id: UUID,
        run_id: UUID,
        snapshot_id: UUID,
        tenant_id: str,
        source_role: AgentSourceRole,
        selected_entities: frozenset[AgentEntityKind],
        field_mapping: dict[str, str] | None = None,
        page_size: int = 500,
    ) -> AgentIngestionOutcome:
        configuration = connector.configuration
        if not isinstance(configuration, DatabaseConnectorConfiguration):
            raise TypeError("database ingestion requires a database connector")
        if configuration.source_role != source_role.value:
            raise ValueError("database connector source role changed after task creation")

        frozen_mapping = field_mapping or configuration.field_columns
        if not frozen_mapping:
            raise ValueError("database ingestion requires a frozen field mapping")

        records: list[AgentContractRecord] = []
        marks: list[AgentInputMark] = []
        stable_order = 0
        async for page in connector.read_pages(
            page_size=page_size,
            fields=tuple(dict.fromkeys(frozen_mapping.values())),
        ):
            for row in page.records:
                stable_order += 1
                identifier = row.get(configuration.primary_key)
                if identifier is None or not str(identifier).strip():
                    raise ValueError("database connector row lacks a stable primary key")
                projected = self._mapper.map_row(
                    task_id=task_id,
                    run_id=run_id,
                    snapshot_id=snapshot_id,
                    tenant_id=tenant_id,
                    source_role=source_role,
                    row_number=stable_order + 1,
                    row=row,
                    field_mapping=frozen_mapping,
                ).model_copy(
                    update={
                        "stable_locator": (f"database:{connector_id}:{str(identifier).strip()}"),
                        "stable_order": stable_order,
                        "raw_row_number": None,
                    }
                )
                if projected.entity_kind not in selected_entities:
                    continue
                records.append(projected)
                mark = self._mapper.validation_mark(projected)
                if mark is not None:
                    marks.append(mark)
        return AgentIngestionOutcome(records=tuple(records), marks=tuple(marks))
