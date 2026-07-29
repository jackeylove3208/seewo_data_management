"""CSV reader adapter for the new Agent ingestion contract."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from app.ingestion.agent_contract import AgentContractError, AgentContractMapper
from app.ingestion.csv_reader import inspect_csv, read_csv_frame
from app.schemas.agent_ingestion import (
    AgentContractRecord,
    AgentEntityKind,
    AgentInputMark,
    AgentSourceRole,
)


@dataclass(frozen=True)
class AgentIngestionOutcome:
    records: tuple[AgentContractRecord, ...]
    marks: tuple[AgentInputMark, ...]


def _target_stable_locator(row: Mapping[str, object], row_number: int) -> str:
    id_values = [
        value
        for key, value in row.items()
        if str(key).strip().casefold() == "id"
    ]
    if not id_values:
        return f"csv:{row_number}"
    value = id_values[0]
    locator = str(value).strip() if value is not None else ""
    if not locator:
        raise AgentContractError("target CSV requires a non-empty stable id")
    return locator


class AgentCsvIngestionAdapter:
    """Projects CSV data without invoking legacy task creation or validation."""

    def __init__(self, mapper: AgentContractMapper | None = None) -> None:
        self._mapper = mapper or AgentContractMapper()

    def inspect_csv(
        self,
        *,
        path: Path,
        task_id: UUID,
        run_id: UUID,
        snapshot_id: UUID,
        tenant_id: str,
        source_role: AgentSourceRole,
        selected_entities: frozenset[AgentEntityKind],
        field_mapping: Mapping[str, str] | None = None,
    ) -> AgentIngestionOutcome:
        inspection = inspect_csv(path)
        if field_mapping is None:
            self._mapper.assert_recognizable_headers(inspection.headers)
        frame = read_csv_frame(path, inspection)
        records: list[AgentContractRecord] = []
        marks: list[AgentInputMark] = []
        seen_target_locators: set[str] = set()
        for raw_row in frame.to_dicts():
            row_number = int(raw_row.pop("_row_number"))
            locator = (
                _target_stable_locator(raw_row, row_number)
                if source_role is AgentSourceRole.TARGET
                else f"csv:{row_number}"
            )
            if source_role is AgentSourceRole.TARGET:
                if locator in seen_target_locators:
                    raise AgentContractError(
                        "target CSV requires unique stable row identifiers"
                    )
                seen_target_locators.add(locator)
            record = self._mapper.map_row(
                task_id=task_id,
                run_id=run_id,
                snapshot_id=snapshot_id,
                tenant_id=tenant_id,
                source_role=source_role,
                row_number=row_number,
                row=raw_row,
                field_mapping=field_mapping,
            ).model_copy(update={"stable_locator": locator})
            if record.entity_kind not in selected_entities:
                continue
            records.append(record)
            mark = self._mapper.validation_mark(record)
            if mark is not None:
                marks.append(mark)
        return AgentIngestionOutcome(records=tuple(records), marks=tuple(marks))
