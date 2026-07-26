import hashlib
import json
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.observability import agent_observability
from app.agent_runtime.repository import AgentRuntimeRepository, SchoolLockConflict
from app.agent_runtime.service import AgentSupervisorService
from app.core.config import Settings
from app.core.security import OperatorContext
from app.local_sources.service import LocalSourceService
from app.models.agent_runtime import AgentRunRecord, SchoolTaskLockRecord
from app.models.reconciliation import ReconciliationTask
from app.models.reporting import AgentReportRecord
from app.models.snapshots import Snapshot, SourceFile
from app.repositories.files import FileRepository
from app.schemas.agent_api import AgentTaskIntent
from app.schemas.canonical_entities import SourceRole


class AgentTaskConflict(ValueError):
    pass


class AgentConnectorCapabilityFailure(ValueError):
    pass


class AgentTaskService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        operator: OperatorContext,
        settings: Settings | None = None,
    ) -> None:
        self.session = session
        self.operator = operator
        self.settings = settings
        self.runtime = AgentRuntimeRepository(session)

    async def create(
        self,
        intent: AgentTaskIntent,
        *,
        idempotency_key: str,
        conversation_id: UUID | None = None,
    ) -> tuple[ReconciliationTask, AgentRunRecord]:
        self._validate_connector_runtime(intent)
        payload = intent.model_dump(mode="json")
        request_hash = _hash({"tenant_id": self.operator.tenant_id, **payload})
        existing = await self.session.scalar(
            select(ReconciliationTask).where(
                ReconciliationTask.idempotency_key == idempotency_key
            )
        )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise AgentTaskConflict("idempotency key was used for another Agent task")
            run = await self.runtime.get_run_for_task(existing.id)
            if run is None:
                raise AgentTaskConflict("Agent task exists without a runtime")
            return existing, run

        active_lock = await self.session.scalar(
            select(SchoolTaskLockRecord).where(
                SchoolTaskLockRecord.tenant_id == self.operator.tenant_id,
                SchoolTaskLockRecord.active.is_(True),
            )
        )
        if active_lock is not None:
            raise SchoolLockConflict(active_lock.owner_task_id)

        workflow_version = (
            self.settings.new_task_workflow_version
            if self.settings is not None
            else "new-agent-v1"
        )
        task = ReconciliationTask(
            id=uuid4(),
            tenant_id=self.operator.tenant_id,
            scope_id="all",
            snapshot_mode="full",
            entity_types=sorted(item.value for item in intent.entity_types),
            status="created",
            stage="ingestion",
            workflow_version=workflow_version,
            task_kind="sync",
            title=intent.title.strip(),
            agent_intent=payload,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        self.session.add(task)
        await self.session.flush()
        if intent.source.kind == "csv" and intent.target.kind == "csv":
            assert intent.source.upload_id is not None
            assert intent.target.upload_id is not None
            await self._bind_csv_pair(
                task,
                source_id=intent.source.upload_id,
                target_id=intent.target.upload_id,
            )
        elif intent.source.kind == "local" and intent.target.kind == "local":
            await self._bind_local_pair(
                task,
                source_ref=intent.source.source_ref,
                target_ref=intent.target.source_ref,
            )
        elif intent.source.kind == "csv" and intent.target.kind == "local":
            assert intent.source.upload_id is not None
            await self._bind_uploaded_source_local_target(
                task,
                source_id=intent.source.upload_id,
                target_ref=intent.target.source_ref,
            )
        run = await AgentSupervisorService(
            self.session, operator=self.operator, repository=self.runtime
        ).start(task_id=task.id, conversation_id=conversation_id)
        return task, run

    def _validate_connector_runtime(self, intent: AgentTaskIntent) -> None:
        configured = tuple(
            selection
            for selection in (intent.source, intent.target)
            if selection.kind not in {"csv", "local"}
        )
        if not configured:
            return
        # The safe connector façades are available, but a configured connector must not be
        # accepted until the durable Agent worker can materialize its immutable input evidence
        # and bind its mutation adapter.  Failing before task/lock creation prevents a task from
        # becoming permanently stuck in ingestion.
        agent_observability.observe(
            "connector_failed",
            connector_kind="+".join(sorted(selection.kind for selection in configured)),
            outcome="rejected",
            error_code="durable_runtime_unavailable",
        )
        raise AgentConnectorCapabilityFailure(
            "Configured API/database connector is not available in the durable Agent runtime"
        )

    async def _bind_local_pair(
        self,
        task: ReconciliationTask,
        *,
        source_ref: str | None,
        target_ref: str | None,
    ) -> None:
        if self.settings is None or source_ref is None or target_ref is None:
            raise ValueError("local Agent task requires configured source references")
        sources = LocalSourceService(self.settings)
        source_material = sources.describe(source_ref)
        target_material = sources.describe_target_for_write(target_ref)
        if source_material.path == target_material.path:
            raise ValueError("Agent task requires two different local sources")
        files = FileRepository(self.session)
        source = await files.create(
            source_role=SourceRole.AUTHORITATIVE,
            original_name=source_material.path.name,
            storage_name=f"local-{uuid4().hex}",
            storage_path=source_material.path,
            sha256=source_material.sha256,
            size_bytes=source_material.size_bytes,
            detected_encoding="utf-8",
            managed_storage=False,
        )
        target = await files.create(
            source_role=SourceRole.TARGET,
            original_name=target_material.path.name,
            storage_name=f"local-{uuid4().hex}",
            storage_path=target_material.path,
            sha256=target_material.sha256,
            size_bytes=target_material.size_bytes,
            detected_encoding="utf-8",
            managed_storage=False,
        )
        await self.session.flush()
        await files.bind_to_task(source.id, task.id)
        await files.bind_to_task(target.id, task.id)
        self.session.add_all((_agent_snapshot(task.id, source), _agent_snapshot(task.id, target)))
        await self.session.flush()

    async def _bind_uploaded_source_local_target(
        self,
        task: ReconciliationTask,
        *,
        source_id: UUID,
        target_ref: str | None,
    ) -> None:
        if self.settings is None or target_ref is None:
            raise ValueError("local Agent target requires a configured source reference")
        files = FileRepository(self.session)
        source = await files.get(source_id)
        if source is None:
            raise LookupError("Agent CSV upload not found")
        if source.source_role != SourceRole.AUTHORITATIVE.value:
            raise ValueError("Agent CSV upload role mismatch")
        target_material = LocalSourceService(self.settings).describe_target_for_write(
            target_ref
        )
        target = await files.create(
            source_role=SourceRole.TARGET,
            original_name=target_material.path.name,
            storage_name=f"local-{uuid4().hex}",
            storage_path=target_material.path,
            sha256=target_material.sha256,
            size_bytes=target_material.size_bytes,
            detected_encoding="utf-8",
            managed_storage=False,
        )
        await self.session.flush()
        await files.bind_to_task(source.id, task.id)
        await files.bind_to_task(target.id, task.id)
        self.session.add_all(
            (_agent_snapshot(task.id, source), _agent_snapshot(task.id, target))
        )
        await self.session.flush()

    async def get(self, task_id: UUID) -> tuple[ReconciliationTask, AgentRunRecord]:
        task = await self.session.scalar(
            select(ReconciliationTask).where(
                ReconciliationTask.id == task_id,
                ReconciliationTask.tenant_id == self.operator.tenant_id,
                ReconciliationTask.workflow_version.in_(("new-agent-v1", "agent-graph-v1")),
            )
        )
        if task is None:
            raise LookupError("Agent task not found")
        run = await self.runtime.get_run_for_task(task.id)
        if run is None:
            raise LookupError("Agent runtime not found")
        return task, run

    async def report_id(self, task_id: UUID) -> UUID | None:
        return cast(
            UUID | None,
            await self.session.scalar(
                select(AgentReportRecord.id).where(AgentReportRecord.task_id == task_id)
            ),
        )

    async def _bind_csv_pair(
        self, task: ReconciliationTask, *, source_id: UUID, target_id: UUID
    ) -> None:
        files = FileRepository(self.session)
        source = await files.get(source_id)
        target = await files.get(target_id)
        if source is None or target is None:
            raise LookupError("Agent CSV upload not found")
        if source.source_role != "authoritative" or target.source_role != "target":
            raise ValueError("Agent CSV upload role mismatch")
        if source.id == target.id:
            raise ValueError("Agent task requires two CSV uploads")
        await files.bind_to_task(source.id, task.id)
        await files.bind_to_task(target.id, task.id)
        self.session.add_all(
            (
                _agent_snapshot(task.id, source),
                _agent_snapshot(task.id, target),
            )
        )
        await self.session.flush()


def _agent_snapshot(task_id: UUID, source: SourceFile) -> Snapshot:
    return Snapshot(
        id=uuid4(),
        task_id=task_id,
        source_file_id=source.id,
        source_role=source.source_role,
        schema_version="agent-contract-v1",
        mapping_version="agent-csv-v1",
        file_hash=source.sha256,
        content_hash=_hash({"source_file_id": str(source.id), "sha256": source.sha256}),
        state="published",
        summary={"total": 0, "accepted": 0, "warnings": 0, "quarantined": 0},
    )


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
