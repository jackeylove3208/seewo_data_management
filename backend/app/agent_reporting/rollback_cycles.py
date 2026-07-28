from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.reconciliation import ReconciliationTask
from app.models.reporting import AgentReportRecord, AgentRollbackCycleRecord

ROLLBACK_ALREADY_PERFORMED = "already_rolled_back"


class RollbackAlreadyPerformed(ValueError):
    pass


class RollbackCycleChanged(ValueError):
    pass


@dataclass(frozen=True)
class TargetDataSource:
    key: str
    kind: str


class AgentRollbackCycleService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._historical_status_cache: dict[tuple[str, str], bool] = {}

    async def blocked_reason(self, task: ReconciliationTask) -> str | None:
        identity = target_data_source(task)
        if identity is None or task.task_kind == "rollback":
            return None
        cycle = await self._cycle(task.tenant_id, identity.key)
        blocked = (
            cycle.completed_rollback_task_id is not None
            if cycle is not None
            else await self._historical_blocked(task.tenant_id, identity.key)
        )
        if blocked:
            return ROLLBACK_ALREADY_PERFORMED
        return None

    async def ensure_available(
        self,
        task: ReconciliationTask,
        *,
        expected_generation: int | None = None,
    ) -> int:
        identity = target_data_source(task)
        if identity is None:
            return 0
        cycle = await self._cycle(task.tenant_id, identity.key, for_update=True)
        generation = cycle.generation if cycle is not None else 0
        if expected_generation is not None and generation != expected_generation:
            raise RollbackCycleChanged(
                "target data source sync cycle changed after rollback preview"
            )
        blocked = (
            cycle.completed_rollback_task_id is not None
            if cycle is not None
            else await self._historical_blocked(task.tenant_id, identity.key)
        )
        if blocked:
            raise RollbackAlreadyPerformed(
                "target data source was already rolled back; "
                "a fully successful sync is required before another rollback"
            )
        return generation

    async def record_fully_successful_sync(self, task: ReconciliationTask) -> None:
        identity = target_data_source(task)
        if identity is None:
            return
        cycle = await self._cycle(task.tenant_id, identity.key, for_update=True)
        now = datetime.now(UTC)
        if cycle is None:
            self.session.add(
                AgentRollbackCycleRecord(
                    tenant_id=task.tenant_id,
                    data_source_key=identity.key,
                    target_kind=identity.kind,
                    generation=1,
                    latest_successful_sync_task_id=task.id,
                    completed_rollback_task_id=None,
                    completed_rollback_at=None,
                    updated_at=now,
                )
            )
        else:
            cycle.target_kind = identity.kind
            cycle.generation += 1
            cycle.latest_successful_sync_task_id = task.id
            cycle.completed_rollback_task_id = None
            cycle.completed_rollback_at = None
            cycle.updated_at = now
        await self.session.flush()

    async def record_completed_rollback(self, task: ReconciliationTask) -> None:
        identity = target_data_source(task)
        if identity is None:
            return
        cycle = await self._cycle(task.tenant_id, identity.key, for_update=True)
        now = datetime.now(UTC)
        source_task_id = _source_task_id(task)
        if cycle is None:
            self.session.add(
                AgentRollbackCycleRecord(
                    tenant_id=task.tenant_id,
                    data_source_key=identity.key,
                    target_kind=identity.kind,
                    generation=1,
                    latest_successful_sync_task_id=source_task_id,
                    completed_rollback_task_id=task.id,
                    completed_rollback_at=now,
                    updated_at=now,
                )
            )
        elif cycle.completed_rollback_task_id is None:
            cycle.completed_rollback_task_id = task.id
            cycle.completed_rollback_at = now
            cycle.updated_at = now
        await self.session.flush()

    async def _cycle(
        self,
        tenant_id: str,
        data_source_key: str,
        *,
        for_update: bool = False,
    ) -> AgentRollbackCycleRecord | None:
        statement = select(AgentRollbackCycleRecord).where(
            AgentRollbackCycleRecord.tenant_id == tenant_id,
            AgentRollbackCycleRecord.data_source_key == data_source_key,
        )
        if for_update:
            statement = statement.with_for_update()
        return cast(
            AgentRollbackCycleRecord | None,
            await self.session.scalar(statement),
        )

    async def _historical_blocked(
        self,
        tenant_id: str,
        data_source_key: str,
    ) -> bool:
        cache_key = (tenant_id, data_source_key)
        cached = self._historical_status_cache.get(cache_key)
        if cached is not None:
            return cached
        rows = (
            await self.session.execute(
                select(ReconciliationTask, AgentReportRecord)
                .join(
                    AgentReportRecord,
                    AgentReportRecord.task_id == ReconciliationTask.id,
                )
                .where(ReconciliationTask.tenant_id == tenant_id)
                .order_by(
                    AgentReportRecord.created_at.desc(),
                    AgentReportRecord.id.desc(),
                )
            )
        ).all()
        blocked = False
        for task, report in rows:
            identity = target_data_source(task)
            if identity is None or identity.key != data_source_key:
                continue
            if task.task_kind == "rollback" and report.terminal_state == "completed":
                blocked = True
                break
            if is_fully_successful_sync(task, report.terminal_state, report.facts):
                break
        self._historical_status_cache[cache_key] = blocked
        return blocked


def target_data_source(task: ReconciliationTask) -> TargetDataSource | None:
    intent = task.agent_intent if isinstance(task.agent_intent, dict) else {}
    target = intent.get("target")
    if not isinstance(target, dict):
        return None
    kind = target.get("kind")
    reference: Any
    if kind == "csv":
        reference = target.get("upload_id")
    elif kind in {"api", "database"}:
        reference = target.get("configuration_id")
    elif kind == "local":
        reference = target.get("source_ref")
    else:
        return None
    if not isinstance(reference, str) or not reference:
        return None
    canonical = json.dumps(
        {"kind": kind, "reference": reference},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return TargetDataSource(
        key=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        kind=kind,
    )


def require_rollback_cycle_generation(task: ReconciliationTask) -> int:
    intent = task.agent_intent if isinstance(task.agent_intent, dict) else {}
    generation = intent.get("rollback_cycle_generation")
    if not isinstance(generation, int):
        raise RollbackCycleChanged(
            "rollback preview is missing sync cycle generation"
        )
    return generation


def is_fully_successful_sync(
    task: ReconciliationTask,
    terminal_state: str,
    facts: Mapping[str, Any],
) -> bool:
    return task.task_kind == "sync" and has_fully_verified_mutations(
        terminal_state,
        facts,
    )


def has_fully_verified_mutations(
    terminal_state: str,
    facts: Mapping[str, Any],
) -> bool:
    if terminal_state != "completed":
        return False
    mutations = facts.get("mutations")
    if not isinstance(mutations, list) or not mutations:
        return False
    return all(
        isinstance(mutation, dict)
        and mutation.get("status") == "succeeded"
        and isinstance(mutation.get("verification"), dict)
        and mutation["verification"].get("valid") is True
        for mutation in mutations
    )


def _source_task_id(task: ReconciliationTask) -> UUID:
    intent = task.agent_intent if isinstance(task.agent_intent, dict) else {}
    raw = intent.get("source_task_id")
    if isinstance(raw, str):
        return UUID(raw)
    return task.parent_task_id or task.id
