"""Durable orchestration for publishing verified Agent versions to local targets."""

import asyncio
import hashlib
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.repository import AgentRuntimeRepository
from app.agent_runtime.state_machine import AgentPhase
from app.core.config import Settings
from app.local_sources.publisher import LocalCsvPublicationConflict, LocalCsvPublisher
from app.local_sources.service import LocalSourceService
from app.models.agent_runtime import AgentRunRecord
from app.models.executions import TargetVersionRecord
from app.models.reconciliation import ReconciliationTask
from app.models.snapshots import SourceFile

_CHECKPOINT_KEY = "agent-local-publication-v1"


async def publish_local_target(
    session: AsyncSession,
    *,
    settings: Settings,
    task_id: UUID,
    run_id: UUID,
    phase: AgentPhase,
    target_version_id: UUID | None,
) -> dict[str, Any]:
    task = await session.get(ReconciliationTask, task_id)
    run = await session.get(AgentRunRecord, run_id)
    if task is None or run is None or run.task_id != task.id:
        raise LookupError("local publication task context is missing")
    source_task = task
    if task.task_kind == "rollback":
        if task.parent_task_id is None:
            raise LookupError("rollback local publication source task is missing")
        rollback_source_task = await session.get(
            ReconciliationTask, task.parent_task_id
        )
        if rollback_source_task is None:
            raise LookupError("rollback local publication source task is missing")
        source_task = rollback_source_task
    target_intent = (source_task.agent_intent or {}).get("target", {})
    if not isinstance(target_intent, dict) or target_intent.get("kind") != "local":
        return {"status": "not_applicable"}
    source_ref = target_intent.get("source_ref")
    if not isinstance(source_ref, str):
        raise LookupError("local publication target reference is missing")
    sources = LocalSourceService(settings)
    sources.describe_target_for_write(source_ref)
    runtime = AgentRuntimeRepository(session)

    if target_version_id is None:
        input_hash = _input_hash(f"no-changes:{task.request_hash}")
        payload = {
            "status": "no_changes",
            "source_ref": source_ref,
            "target_version_id": None,
        }
        existing = await runtime.get_checkpoint(
            run_id,
            phase=phase,
            checkpoint_key=_CHECKPOINT_KEY,
        )
        if existing is not None:
            if existing.input_hash != input_hash:
                raise LocalCsvPublicationConflict("publication_checkpoint_conflict")
            return dict(existing.payload)
        await runtime.save_checkpoint(
            run_id,
            phase=phase,
            checkpoint_key=_CHECKPOINT_KEY,
            input_hash=input_hash,
            payload=payload,
        )
        return payload

    version = await session.get(TargetVersionRecord, target_version_id)
    if (
        version is None
        or version.task_id != source_task.id
        or version.tenant_id != source_task.tenant_id
    ):
        raise LookupError("verified local target version is missing")
    input_hash = f"sha256:{version.file_sha256}"
    existing = await runtime.get_checkpoint(
        run_id,
        phase=phase,
        checkpoint_key=_CHECKPOINT_KEY,
    )
    if existing is not None:
        if existing.input_hash != input_hash:
            raise LocalCsvPublicationConflict("publication_checkpoint_conflict")
        return dict(existing.payload)

    expected_destination_sha256 = await _expected_destination_hash(
        session,
        task=task,
        source_task=source_task,
    )
    result = await asyncio.to_thread(
        LocalCsvPublisher(sources).publish,
        source_ref=source_ref,
        managed_version_path=Path(version.storage_path),
        expected_destination_sha256=expected_destination_sha256,
        target_version_id=version.id,
    )
    payload = {
        "status": result.status,
        "source_ref": result.source_ref,
        "target_version_id": str(result.target_version_id),
        "expected_destination_sha256": result.expected_destination_sha256,
        "published_sha256": result.published_sha256,
    }
    await runtime.save_checkpoint(
        run_id,
        phase=phase,
        checkpoint_key=_CHECKPOINT_KEY,
        input_hash=input_hash,
        payload=payload,
    )
    await runtime.append_event(
        run_id,
        "local_target_published",
        {
            "source_ref": result.source_ref,
            "target_version_id": str(result.target_version_id),
            "status": result.status,
        },
    )
    return payload


async def _expected_destination_hash(
    session: AsyncSession,
    *,
    task: ReconciliationTask,
    source_task: ReconciliationTask,
) -> str:
    if task.task_kind == "rollback":
        intent = task.agent_intent or {}
        target_version_id = intent.get(
            "comparison_target_version_id",
            intent.get("target_version_id"),
        )
        if target_version_id is None:
            raise LookupError("rollback publication input version is missing")
        input_version = await session.get(
            TargetVersionRecord,
            UUID(str(target_version_id)),
        )
        if input_version is None or input_version.task_id != source_task.id:
            raise LookupError("rollback publication input version is missing")
        return input_version.file_sha256
    target_file = await session.scalar(
        select(SourceFile).where(
            SourceFile.task_id == source_task.id,
            SourceFile.source_role == "target",
        )
    )
    if target_file is None:
        raise LookupError("local publication target file fact is missing")
    return target_file.sha256


def _input_hash(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode()).hexdigest()}"
