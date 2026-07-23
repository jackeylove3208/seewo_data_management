"""Independent, school-exclusive rollback handlers for verified Agent CSV facts."""

from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_reporting.service import AgentReportingService
from app.agent_runtime.csv_governance_handlers import AgentTargetVersionRepository
from app.agent_runtime.observability import agent_observability
from app.agent_runtime.repository import AgentRuntimeRepository
from app.agent_runtime.state_machine import AgentPhase
from app.agent_runtime.worker import AgentWorkContext, AgentWorkResult
from app.executions.agent_service import AgentExecutionService, CsvAgentTargetAdapter
from app.executions.csv_versioning import CsvTargetVersioner
from app.governance.agent_governance import AgentGovernanceOperation, AgentOperation
from app.models.executions import TargetVersionRecord
from app.models.reconciliation import ReconciliationTask
from app.repositories.executions import ExecutionRepository


class CsvRollbackHandlers:
    def __init__(self, *, output_root: Path) -> None:
        self._output_root = output_root

    async def plan(
        self, session: AsyncSession, context: AgentWorkContext
    ) -> AgentWorkResult:
        task = await session.get(ReconciliationTask, context.task_id)
        if task is None or not task.agent_intent:
            raise LookupError("rollback task facts are missing")
        operations = list(task.agent_intent.get("operations", []))
        target_version_id = UUID(str(task.agent_intent["target_version_id"]))
        target = await session.get(TargetVersionRecord, target_version_id)
        if target is None:
            raise LookupError("rollback target version is missing")
        current = await ExecutionRepository(session).current_target_version(
            target.task_id
        )
        if current is None or current.id != target.id:
            raise ValueError("rollback target version has an intervening change")
        await AgentRuntimeRepository(session).save_checkpoint(
            context.run_id,
            phase=AgentPhase.PLAN_RESTORE,
            checkpoint_key="agent-csv-rollback-plan-v1",
            input_hash=str(task.request_hash),
            payload={
                "source_task_id": str(task.parent_task_id),
                "target_version_id": str(target.id),
                "operations": operations,
            },
        )
        return AgentWorkResult(next_phase=AgentPhase.CLARIFY_RESTORE_CONFLICTS)

    async def clarify(self, _session: AsyncSession, _context: AgentWorkContext) -> AgentWorkResult:
        return AgentWorkResult(next_phase=AgentPhase.APPROVE_RESTORE)

    async def approve(self, _session: AsyncSession, _context: AgentWorkContext) -> AgentWorkResult:
        return AgentWorkResult(next_phase=AgentPhase.EXECUTE_RESTORE)

    async def execute(
        self, session: AsyncSession, context: AgentWorkContext
    ) -> AgentWorkResult:
        task = await session.get(ReconciliationTask, context.task_id)
        if task is None or not task.agent_intent:
            raise LookupError("rollback task facts are missing")
        parent = await session.get(
            TargetVersionRecord, UUID(str(task.agent_intent["target_version_id"]))
        )
        if parent is None:
            raise LookupError("rollback target version is missing")
        operations = tuple(
            _rollback_operation(item, target_version=f"sha256:{parent.file_sha256}")
            for item in task.agent_intent.get("operations", [])
        )
        if not operations:
            raise ValueError("rollback has no verified operations")
        versioner = CsvTargetVersioner(
            repository=AgentTargetVersionRepository(ExecutionRepository(session)),
            output_root=self._output_root,
        )
        result = await AgentExecutionService().execute(
            plan_id=uuid5(NAMESPACE_URL, f"agent-rollback:{task.id}"),
            target_version=f"sha256:{parent.file_sha256}",
            operations=operations,
            target=CsvAgentTargetAdapter(versioner=versioner, parent=parent),
        )
        output = result.output_target_version
        facts: dict[str, Any] = {
            "source_task_id": str(task.parent_task_id),
            "mutations": [
                {
                    "id": str(item.operation_id),
                    "status": item.status,
                    "verification": {"valid": item.status == "succeeded"},
                    "compensation_for": str(operation.finding_id),
                }
                for operation, item in (
                    (operation, result.by_operation[operation.id]) for operation in operations
                )
            ],
        }
        if isinstance(output, TargetVersionRecord):
            facts.update(
                {
                    "output_target_version_id": str(output.id),
                    "output_target_path": output.storage_path,
                }
            )
        await AgentRuntimeRepository(session).save_checkpoint(
            context.run_id,
            phase=AgentPhase.EXECUTE_RESTORE,
            checkpoint_key="agent-csv-rollback-execution-v1",
            input_hash=str(task.request_hash),
            payload=facts,
        )
        return AgentWorkResult(next_phase=AgentPhase.REPORT_RESTORE)

    async def report(
        self, session: AsyncSession, context: AgentWorkContext
    ) -> AgentWorkResult:
        task = await session.get(ReconciliationTask, context.task_id)
        if task is None:
            raise LookupError("rollback task is missing")
        checkpoint = await AgentRuntimeRepository(session).get_checkpoint(
            context.run_id,
            phase=AgentPhase.EXECUTE_RESTORE,
            checkpoint_key="agent-csv-rollback-execution-v1",
        )
        facts = checkpoint.payload if checkpoint is not None else {"mutations": []}
        report = await AgentReportingService(session).generate(
            task_id=task.id,
            tenant_id=task.tenant_id,
            kind="rollback",
            terminal_state="completed",
            facts=facts,
        )
        task.status = "completed"
        task.stage = "terminal"
        await AgentRuntimeRepository(session).append_event(
            context.run_id,
            "report_ready",
            {"report_id": str(report.id), "terminal_state": "completed"},
        )
        agent_observability.observe(
            "rollback_completed",
            task_id=task.id,
            run_id=context.run_id,
            phase=AgentPhase.REPORT_RESTORE.value,
            mutation_count=len(facts.get("mutations", [])),
            outcome="completed",
        )
        return AgentWorkResult(next_phase=AgentPhase.TERMINAL)


def _rollback_operation(
    mutation: dict[str, Any], *, target_version: str
) -> AgentGovernanceOperation:
    original = AgentOperation(str(mutation["operation"]))
    before = mutation.get("before")
    after = mutation.get("after")
    identifier = mutation.get("target_source_identifier")
    if original == AgentOperation.CREATE:
        operation = AgentOperation(AgentOperation.DELETE)
        identifier = identifier or (after or {}).get("source_id")
        restore_before, restore_after = after, None
    elif original == AgentOperation.DELETE:
        operation = AgentOperation(AgentOperation.CREATE)
        restore_before, restore_after = None, dict(before or {})
        restore_after["source_id"] = identifier
        identifier = None
    else:
        operation = AgentOperation(AgentOperation.UPDATE)
        restore_after = dict(before or {})
        restore_before = {
            field: (after or {}).get(field) for field in restore_after
        }
    mutation_id = UUID(str(mutation["id"]))
    return AgentGovernanceOperation(
        id=uuid5(NAMESPACE_URL, f"agent-rollback-operation:{mutation_id}"),
        finding_id=mutation_id,
        operation=operation,
        entity_kind=str(mutation["entity_kind"]),
        target_source_identifier=identifier,
        before=restore_before,
        after=restore_after,
        dependencies=frozenset(),
        risk="high",
        target_version=target_version,
    )
