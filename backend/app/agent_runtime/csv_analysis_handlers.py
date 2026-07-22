"""Analysis-only CSV phase handlers for the new Agent workflow."""

from hashlib import sha256
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.repository import AgentRuntimeRepository
from app.agent_runtime.state_machine import AgentPhase
from app.ingestion.agent_csv_adapter import AgentCsvIngestionAdapter, AgentIngestionOutcome
from app.models.agent_analysis import AgentInputRecord
from app.models.agent_runtime import AgentRunRecord
from app.models.reconciliation import ReconciliationTask
from app.models.snapshots import Snapshot, SourceFile
from app.repositories.agent_analysis import AgentAnalysisRepository
from app.schemas.agent_ingestion import AgentEntityKind, AgentInputMark, AgentSourceRole


class AgentIngestionPhaseHandler:
    """Persist safe Agent projections from immutable CSV source files."""

    def __init__(
        self, session: AsyncSession, adapter: AgentCsvIngestionAdapter | None = None
    ) -> None:
        self._session = session
        self._adapter = adapter or AgentCsvIngestionAdapter()
        self._runtime = AgentRuntimeRepository(session)
        self._analysis = AgentAnalysisRepository(session)

    async def ingest(self, *, run_id: UUID) -> None:
        run = await self._session.get(AgentRunRecord, run_id)
        if run is None:
            raise LookupError(f"agent run not found: {run_id}")
        task = await self._session.get(ReconciliationTask, run.task_id)
        if task is None or task.workflow_version != "new-agent-v1":
            raise ValueError("CSV Agent ingestion requires a new-agent-v1 task")
        selected = _selected_entities(task.entity_types)
        snapshots = tuple(
            await self._session.scalars(
                select(Snapshot).where(Snapshot.task_id == task.id).order_by(Snapshot.source_role)
            )
        )
        by_role = {snapshot.source_role: snapshot for snapshot in snapshots}
        if set(by_role) != {"authoritative", "target"}:
            raise ValueError("Agent ingestion requires authoritative and target snapshots")

        total_records = 0
        total_marks = 0
        material_hashes: list[str] = []
        for role in (AgentSourceRole.AUTHORITATIVE, AgentSourceRole.TARGET):
            snapshot = by_role[role.value]
            source = await self._session.get(SourceFile, snapshot.source_file_id)
            if source is None:
                raise ValueError("snapshot source file is unavailable")
            await self._analysis.persist_capability(
                run_id=run.id,
                task_id=task.id,
                tenant_id=task.tenant_id,
                source_role=role.value,
                connector_kind="csv",
                capabilities={"read": True, "write": role is AgentSourceRole.TARGET},
            )
            outcome = self._adapter.inspect_csv(
                path=Path(source.storage_path),
                task_id=task.id,
                run_id=run.id,
                snapshot_id=snapshot.id,
                tenant_id=task.tenant_id,
                source_role=role,
                selected_entities=selected,
            )
            persisted = await self._analysis.persist_inputs(outcome.records)
            await self._analysis.persist_marks(_bind_marks(outcome, persisted))
            total_records += len(persisted)
            total_marks += len(outcome.marks)
            material_hashes.extend(record.input_hash for record in persisted)

        input_hash = sha256("|".join(sorted(material_hashes)).encode()).hexdigest()
        await self._runtime.save_checkpoint(
            run.id,
            phase=AgentPhase.INGEST_AND_NORMALIZE,
            checkpoint_key="agent-csv-ingestion-v1",
            input_hash=input_hash,
            payload={"record_count": total_records, "mark_count": total_marks},
        )
        await self._runtime.append_event(
            run.id,
            "agent_ingestion_persisted",
            {"record_count": total_records, "mark_count": total_marks},
        )


def _bind_marks(
    outcome: AgentIngestionOutcome, persisted: tuple[AgentInputRecord, ...]
) -> tuple[AgentInputMark, ...]:
    by_row = {
        record.raw_row_number: record.id
        for record in persisted
    }
    bound: list[AgentInputMark] = []
    for mark in outcome.marks:
        row_number = mark.safe_evidence.get("row_number")
        if not isinstance(row_number, int):
            raise ValueError("ingestion mark is missing its physical row number")
        input_id = by_row.get(row_number)
        if input_id is None:
            raise ValueError("ingestion mark does not correspond to a persisted input")
        bound.append(mark.model_copy(update={"input_record_id": input_id}))
    return tuple(bound)


def _selected_entities(entity_types: list[str]) -> frozenset[AgentEntityKind]:
    mapping = {
        "department": AgentEntityKind.DEPARTMENT,
        "organization_unit": AgentEntityKind.DEPARTMENT,
        "student": AgentEntityKind.STUDENT,
        "teacher": AgentEntityKind.TEACHER,
    }
    selected = frozenset(mapping[value] for value in entity_types if value in mapping)
    return selected or frozenset(AgentEntityKind)
