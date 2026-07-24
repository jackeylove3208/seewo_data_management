from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_runtime.repository import AgentRuntimeRepository
from app.agent_runtime.state_machine import AgentRunKind
from app.models.agent_runtime import SchoolTaskLockRecord
from app.models.reconciliation import ReconciliationTask
from app.models.reporting import AgentReportRecord


class AgentHistoryPage:
    def __init__(self, items: tuple[AgentReportRecord, ...], *, next_cursor: str | None) -> None:
        self.items = items
        self.next_cursor = next_cursor


class RollbackTaskPreview:
    def __init__(
        self,
        *,
        task_id: UUID,
        task_kind: str,
        report_id: UUID | None,
        target_version_id: UUID,
        operations: tuple[dict[str, Any], ...],
    ) -> None:
        self.task_id = task_id
        self.task_kind = task_kind
        self.report_id = report_id
        self.target_version_id = target_version_id
        self.operations = operations


class AgentReportingService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def generate(
        self,
        *,
        task_id: UUID,
        tenant_id: str,
        kind: str,
        terminal_state: str,
        facts: Mapping[str, Any],
        narrative: Mapping[str, Any] | None = None,
        generated_by: str = "agent-reporting",
    ) -> AgentReportRecord:
        task = await self.session.scalar(
            select(ReconciliationTask).where(
                ReconciliationTask.id == task_id,
                ReconciliationTask.tenant_id == tenant_id,
            )
        )
        if task is None:
            raise LookupError("Agent task not found")
        existing = await self.session.scalar(
            select(AgentReportRecord).where(AgentReportRecord.task_id == task_id)
        )
        if existing is not None:
            return existing
        normalized = _facts(facts, terminal_state)
        digest = _hash(normalized)
        report = AgentReportRecord(
            id=uuid4(),
            task_id=task_id,
            tenant_id=tenant_id,
            kind=kind,
            terminal_state=terminal_state,
            facts=normalized,
            facts_hash=digest,
            content={"terminal_state": terminal_state, "narrative": dict(narrative or {})},
            rollback_eligible=bool(normalized["rollback_evidence"]["eligible"]),
            deletion_eligible=not bool(normalized["rollback_evidence"]["successful_mutation_ids"]),
            generated_by=generated_by,
        )
        self.session.add(report)
        await self.session.flush()
        return report

    async def history(
        self, *, tenant_id: str, limit: int = 50, cursor: str | None = None
    ) -> AgentHistoryPage:
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")
        statement = (
            select(AgentReportRecord)
            .where(AgentReportRecord.tenant_id == tenant_id)
            .order_by(AgentReportRecord.created_at.desc(), AgentReportRecord.id.desc())
            .limit(limit + 1)
        )
        if cursor:
            created_at, report_id = cursor.split("_", 1)
            from datetime import datetime

            statement = statement.where(
                (AgentReportRecord.created_at < datetime.fromisoformat(created_at))
                | (
                    (AgentReportRecord.created_at == datetime.fromisoformat(created_at))
                    & (AgentReportRecord.id < UUID(report_id))
                )
            )
        records = list(await self.session.scalars(statement))
        next_cursor = None
        if len(records) > limit:
            last = records.pop()
            next_cursor = f"{last.created_at.isoformat()}_{last.id}"
        return AgentHistoryPage(tuple(records), next_cursor=next_cursor)

    async def deletion_eligible(self, *, task_id: UUID, tenant_id: str) -> bool:
        report = await self.session.scalar(
            select(AgentReportRecord).where(
                AgentReportRecord.task_id == task_id,
                AgentReportRecord.tenant_id == tenant_id,
            )
        )
        return report is None or report.deletion_eligible

    async def create_rollback_task(
        self,
        *,
        source_task_id: UUID,
        tenant_id: str,
        requested_by: str,
        target_version_id: UUID,
    ) -> RollbackTaskPreview:
        report = await self.session.scalar(
            select(AgentReportRecord).where(
                AgentReportRecord.task_id == source_task_id,
                AgentReportRecord.tenant_id == tenant_id,
            )
        )
        if report is None or not report.rollback_eligible:
            raise ValueError("rollback is not eligible from verified execution facts")
        active = await self.session.scalar(
            select(SchoolTaskLockRecord).where(
                SchoolTaskLockRecord.tenant_id == tenant_id,
                SchoolTaskLockRecord.active.is_(True),
            )
        )
        if active is not None:
            raise ValueError(f"school lock is owned by task {active.owner_task_id}")
        idempotency_key = f"rollback:{source_task_id}:{target_version_id}"
        existing_task = await self.session.scalar(
            select(ReconciliationTask).where(
                ReconciliationTask.idempotency_key == idempotency_key
            )
        )
        if existing_task is not None:
            return RollbackTaskPreview(
                task_id=existing_task.id,
                task_kind=existing_task.task_kind,
                report_id=None,
                target_version_id=target_version_id,
                operations=tuple((existing_task.agent_intent or {}).get("operations", [])),
            )
        source = await self.session.get(ReconciliationTask, source_task_id)
        if source is None:
            raise LookupError("source Agent task not found")
        workflow_version = (
            "agent-graph-v1"
            if source.workflow_version == "agent-graph-v1"
            else "new-agent-v1"
        )
        task = ReconciliationTask(
            id=uuid4(), tenant_id=tenant_id, scope_id=source.scope_id,
            snapshot_mode=source.snapshot_mode, entity_types=list(source.entity_types),
            status="created", stage="rollback", workflow_version=workflow_version,
            task_kind="rollback", parent_task_id=source_task_id,
            title=f"回滚：{source.title or source_task_id}",
            agent_intent={
                "source_task_id": str(source_task_id),
                "target_version_id": str(target_version_id),
                "operations": [],
            },
            idempotency_key=idempotency_key,
            request_hash=_hash(
                {"source_task_id": str(source_task_id), "target_version_id": str(target_version_id)}
            ),
        )
        self.session.add(task)
        await self.session.flush()
        runtime = AgentRuntimeRepository(self.session)
        run = await runtime.create_run(
            task_id=task.id,
            tenant_id=tenant_id,
            conversation_id=None,
            kind=AgentRunKind.ROLLBACK,
            workflow_version=workflow_version,
        )
        if workflow_version == "agent-graph-v1":
            from app.agent_graph.repository import AgentGraphRepository

            await AgentGraphRepository(self.session).create_run_state(
                run_id=run.id,
                graph_version="agent-rollback-graph-v1",
                initial_node="rollback_intent_confirmed",
            )
        evidence = report.facts["rollback_evidence"]["successful_mutations"]
        await runtime.append_event(
            run.id,
            "rollback.preview_created",
            {
                "requested_by": requested_by,
                "source_task_id": str(source_task_id),
                "target_version_id": str(target_version_id),
                "verified_mutation_ids": [str(mutation["id"]) for mutation in evidence],
            },
        )
        operations = tuple(
            {
                **dict(mutation),
                "compensation_for": mutation["id"],
                "target_version_id": str(target_version_id),
                "risk": "high",
            }
            for mutation in evidence
        )
        task.agent_intent = {
            "source_task_id": str(source_task_id),
            "target_version_id": str(target_version_id),
            "operations": list(operations),
        }
        return RollbackTaskPreview(
            task_id=task.id,
            task_kind=task.task_kind,
            report_id=None,
            target_version_id=target_version_id,
            operations=operations,
        )


def _facts(facts: Mapping[str, Any], terminal_state: str) -> dict[str, Any]:
    mutations = list(facts.get("mutations", []))
    counts = {status: 0 for status in ("succeeded", "failed", "blocked", "rejected", "skipped")}
    successful: list[str] = []
    for mutation in mutations:
        status = str(mutation.get("status", "skipped"))
        counts[status] = counts.get(status, 0) + 1
        if status == "succeeded" and mutation.get("verification", {}).get("valid") is True:
            successful.append(str(mutation["id"]))
    result = dict(facts)
    result["mutation_summary"] = counts
    result["rollback_evidence"] = {
        "eligible": terminal_state != "abnormal_input" and bool(successful),
        "reason": "abnormal_input" if terminal_state == "abnormal_input" else None,
        "successful_mutation_ids": successful,
        "successful_mutations": [
            mutation
            for mutation in mutations
            if str(mutation.get("status")) == "succeeded"
            and mutation.get("verification", {}).get("valid") is True
        ],
    }
    return result


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
