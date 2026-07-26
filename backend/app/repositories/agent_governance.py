"""Persistence for the Conversation 3 governance boundary."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.governance.agent_governance import AgentApprovalGroup
from app.models.agent_analysis import (
    AgentApprovalGroupRecord,
    AgentClarificationRecord,
    AgentGovernanceOperationRecord,
    AgentGovernancePlanRecord,
)
from app.models.agent_runtime import AgentRunRecord
from app.models.reconciliation import ReconciliationTask


class GovernanceReplayConflict(ValueError):
    pass


class AgentGovernanceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def save_approval_group(
        self,
        *,
        run: AgentRunRecord,
        task: ReconciliationTask,
        group: AgentApprovalGroup,
    ) -> AgentApprovalGroupRecord:
        if run.task_id != task.id or run.tenant_id != task.tenant_id:
            raise GovernanceReplayConflict("approval context is cross-task or cross-tenant")
        group_key = (
            f"{group.issue_kind}:{group.entity_kind}:{group.operation}:"
            f"{group.policy_version}:fields:"
            f"{','.join(group.changed_fields) or 'none'}:segment:{group.segment_index}"
        )
        existing = await self.session.scalar(
            select(AgentApprovalGroupRecord).where(
                AgentApprovalGroupRecord.run_id == run.id,
                AgentApprovalGroupRecord.group_key == group_key,
            )
        )
        values = {
            "membership_hash": group.membership_hash,
            "finding_ids": [str(item) for item in group.finding_ids],
            "issue_kind": group.issue_kind,
            "entity_kind": group.entity_kind,
            "operation": str(group.operation),
            "policy_version": group.policy_version,
            "risk": group.risk,
        }
        if existing is not None:
            if any(getattr(existing, key) != value for key, value in values.items()):
                existing.status = "stale"
                await self.session.flush()
                raise GovernanceReplayConflict("approval group membership is stale")
            return existing
        record = AgentApprovalGroupRecord(
            id=group.id,
            run_id=run.id,
            task_id=task.id,
            tenant_id=task.tenant_id,
            group_key=group_key,
            status="pending",
            **values,
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def decide_approval(
        self,
        group_id: UUID,
        *,
        membership_hash: str,
        approved: bool,
        actor_id: str,
        reason: str,
    ) -> AgentApprovalGroupRecord:
        record = await self.session.scalar(
            select(AgentApprovalGroupRecord)
            .where(AgentApprovalGroupRecord.id == group_id)
            .with_for_update()
        )
        if record is None:
            raise LookupError("approval group not found")
        if record.membership_hash != membership_hash:
            record.status = "stale"
            await self.session.flush()
            raise GovernanceReplayConflict("approval group is stale")
        if record.status != "pending":
            raise GovernanceReplayConflict("approval group already decided")
        record.status = "approved" if approved else "rejected"
        record.decided_by = actor_id
        record.decision_reason = reason[:1000]
        record.decided_at = datetime.now(UTC)
        record.updated_at = record.decided_at
        await self.session.flush()
        return record

    async def create_clarification(
        self,
        *,
        run: AgentRunRecord,
        task: ReconciliationTask,
        work_item_id: UUID,
        candidates: tuple[dict[str, Any], ...],
        allowed_outcomes: tuple[str, ...],
        batch_id: UUID | None = None,
    ) -> AgentClarificationRecord:
        if run.task_id != task.id or run.tenant_id != task.tenant_id:
            raise GovernanceReplayConflict("clarification context is cross-task or cross-tenant")
        existing = await self.session.scalar(
            select(AgentClarificationRecord).where(
                AgentClarificationRecord.run_id == run.id,
                AgentClarificationRecord.work_item_id == work_item_id,
            )
        )
        if existing is not None:
            if existing.masked_candidates != list(candidates) or existing.allowed_outcomes != list(
                allowed_outcomes
            ):
                raise GovernanceReplayConflict("clarification membership is stale")
            return existing
        record = AgentClarificationRecord(
            id=uuid4(),
            run_id=run.id,
            task_id=task.id,
            tenant_id=task.tenant_id,
            work_item_id=work_item_id,
            batch_id=batch_id,
            masked_candidates=list(candidates),
            allowed_outcomes=list(allowed_outcomes),
            status="pending",
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def record_clarification_interpretation(
        self,
        clarification_id: UUID,
        *,
        original_text: str,
        interpretation: dict[str, Any],
        actor_id: str,
    ) -> AgentClarificationRecord:
        record = await self.session.scalar(
            select(AgentClarificationRecord)
            .where(AgentClarificationRecord.id == clarification_id)
            .with_for_update()
        )
        if record is None:
            raise LookupError("clarification not found")
        if record.status != "pending":
            raise GovernanceReplayConflict("clarification is not awaiting interpretation")
        if not original_text.strip() or len(original_text) > 500:
            raise ValueError("clarification text is invalid")
        outcome = interpretation.get("outcome")
        if outcome not in record.allowed_outcomes and outcome != "use_candidate":
            raise GovernanceReplayConflict("interpretation is outside frozen outcomes")
        record.status = "interpreted"
        record.original_text = original_text.strip()
        record.interpretation = dict(interpretation)
        record.interpreted_by = actor_id
        record.updated_at = datetime.now(UTC)
        await self.session.flush()
        return record

    async def confirm_clarification(
        self,
        clarification_id: UUID,
        *,
        actor_id: str,
        confirmed: bool,
    ) -> AgentClarificationRecord:
        record = await self.session.scalar(
            select(AgentClarificationRecord)
            .where(AgentClarificationRecord.id == clarification_id)
            .with_for_update()
        )
        if record is None:
            raise LookupError("clarification not found")
        if record.status != "interpreted":
            raise GovernanceReplayConflict("second confirmation is required")
        now = datetime.now(UTC)
        record.status = "confirmed" if confirmed else "rejected"
        record.confirmed_by = actor_id
        record.confirmed_at = now
        record.updated_at = now
        await self.session.flush()
        return record

    async def save_plan(
        self,
        *,
        run: AgentRunRecord,
        task: ReconciliationTask,
        source_snapshot_id: UUID,
        target_snapshot_id: UUID,
        target_version: str,
        finding_ids: tuple[UUID, ...],
        operations: list[dict[str, Any]],
        content_hash: str,
        compiled_by: str,
    ) -> AgentGovernancePlanRecord:
        existing = await self.session.scalar(
            select(AgentGovernancePlanRecord).where(
                AgentGovernancePlanRecord.run_id == run.id,
                AgentGovernancePlanRecord.content_hash == content_hash,
            )
        )
        if existing is not None:
            return existing
        plan = AgentGovernancePlanRecord(
            id=uuid4(),
            run_id=run.id,
            task_id=task.id,
            tenant_id=task.tenant_id,
            source_snapshot_id=source_snapshot_id,
            target_snapshot_id=target_snapshot_id,
            target_version=target_version,
            finding_ids=[str(item) for item in finding_ids],
            operations=operations,
            content_hash=content_hash,
            status="compiled",
            compiled_by=compiled_by,
        )
        self.session.add(plan)
        await self.session.flush()
        for operation in operations:
            self.session.add(
                AgentGovernanceOperationRecord(
                    id=UUID(str(operation["id"])),
                    plan_id=plan.id,
                    run_id=run.id,
                    task_id=task.id,
                    finding_id=UUID(str(operation["finding_id"])),
                    operation_type=str(operation["operation"]),
                    entity_kind=str(operation["entity_kind"]),
                    target_source_identifier=operation.get("target_source_identifier"),
                    before=operation.get("before"),
                    after=operation.get("after"),
                    dependencies=[str(item) for item in operation.get("dependencies", ())],
                    risk=str(operation["risk"]),
                    status="pending",
                    attempt_count=0,
                )
            )
        await self.session.flush()
        return plan

    async def record_operation_outcome(
        self,
        operation_id: UUID,
        *,
        status: str,
        attempts: int,
        actual_after: dict[str, Any] | None = None,
        verification: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> AgentGovernanceOperationRecord:
        record = await self.session.scalar(
            select(AgentGovernanceOperationRecord)
            .where(AgentGovernanceOperationRecord.id == operation_id)
            .with_for_update()
        )
        if record is None:
            raise LookupError("Agent governance operation not found")
        if record.status in {"succeeded", "failed", "blocked", "verification_failed"}:
            if record.status != status or record.attempt_count != attempts:
                raise GovernanceReplayConflict("operation outcome is immutable")
            return record
        record.status = status
        record.attempt_count = attempts
        record.actual_after = actual_after
        record.verification = verification
        record.error_code = error_code
        record.updated_at = datetime.now(UTC)
        await self.session.flush()
        return record
