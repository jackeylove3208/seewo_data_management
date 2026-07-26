"""Durable governance and reporting handlers for complete CSV Agent runs."""

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_reporting.service import AgentReportingService
from app.agent_runtime.local_publication import publish_local_target
from app.agent_runtime.observability import agent_observability
from app.agent_runtime.repository import AgentRuntimeRepository
from app.agent_runtime.state_machine import AgentPhase, AgentRunStatus
from app.agent_runtime.worker import AgentWorkContext, AgentWorkResult
from app.core.config import Settings
from app.executions.agent_service import (
    AgentExecutionService,
    CsvAgentTargetAdapter,
)
from app.executions.csv_versioning import CsvTargetVersioner
from app.governance.agent_governance import (
    AgentFindingInput,
    AgentGovernanceOperation,
    AgentOperation,
    compile_agent_plan,
    group_high_risk_findings,
)
from app.local_sources.publisher import copy_managed_initial_version
from app.models.agent_analysis import (
    AgentApprovalGroupRecord,
    AgentClarificationRecord,
    AgentFindingDependencyRecord,
    AgentFindingRecord,
    AgentFindingSolutionRecord,
    AgentGovernanceOperationRecord,
    AgentGovernancePlanRecord,
    AgentIdentityClaimRecord,
    AgentInputMarkRecord,
    AgentInputRecord,
    AgentWorkItemRecord,
)
from app.models.agent_runtime import AgentRunRecord
from app.models.executions import TargetVersionRecord
from app.models.reconciliation import ReconciliationTask
from app.models.snapshots import Snapshot, SourceFile
from app.repositories.agent_governance import AgentGovernanceRepository
from app.repositories.executions import ExecutionRepository


class AgentTargetVersionRepository:
    """Publish Agent CSV versions without pretending they are legacy execution batches."""

    def __init__(self, repository: ExecutionRepository) -> None:
        self._repository = repository

    async def create_target_version(self, **values: Any) -> TargetVersionRecord:
        values["batch_id"] = None
        return await self._repository.create_target_version(**values)


class CsvGovernanceHandlers:
    def __init__(self, *, output_root: Path, settings: Settings | None = None) -> None:
        self._output_root = output_root
        self._settings = settings

    async def aggregate(
        self, session: AsyncSession, context: AgentWorkContext
    ) -> AgentWorkResult:
        run, task, _source, _target, _version, findings = await _finding_inputs(
            session, context.run_id, output_root=self._output_root
        )
        repository = AgentGovernanceRepository(session)
        groups = group_high_risk_findings(findings)
        runtime = AgentRuntimeRepository(session)
        aggregation_hash = _hash(
            [
                {
                    "group_id": str(group.id),
                    "finding_ids": [str(item) for item in group.finding_ids],
                    "membership_hash": group.membership_hash,
                    "policy_version": group.policy_version,
                }
                for group in groups
            ]
        )
        checkpoint = await runtime.get_checkpoint(
            run.id,
            phase=AgentPhase.AGGREGATE_RISK_AND_APPROVALS,
            checkpoint_key="agent-risk-aggregation-v1",
        )
        if checkpoint is not None:
            if checkpoint.input_hash != aggregation_hash:
                raise ValueError("risk aggregation replay changed frozen findings")
            expected_groups = {group.id: group for group in groups}
            saved_groups = tuple(
                await session.scalars(
                    select(AgentApprovalGroupRecord).where(
                        AgentApprovalGroupRecord.run_id == run.id,
                        AgentApprovalGroupRecord.id.in_(expected_groups),
                    )
                )
            )
            if len(saved_groups) != len(expected_groups) or any(
                saved.membership_hash
                != expected_groups[saved.id].membership_hash
                for saved in saved_groups
            ):
                raise ValueError("risk aggregation checkpoint has incomplete approval facts")
            if any(saved.status == "pending" for saved in saved_groups):
                return AgentWorkResult(next_status=AgentRunStatus.WAITING_HUMAN)
            return AgentWorkResult(next_phase=AgentPhase.COMPILE_EXECUTION_PLAN)
        pending = False
        for group in groups:
            saved = await repository.save_approval_group(run=run, task=task, group=group)
            pending = pending or saved.status == "pending"
            if saved.status == "pending":
                await runtime.append_event(
                    run.id,
                    "approval_required",
                    {
                        "group_id": str(saved.id),
                        "issue_kind": saved.issue_kind,
                        "entity_kind": saved.entity_kind,
                        "operation": saved.operation,
                        "item_count": len(saved.finding_ids),
                    },
                )
        await runtime.append_event(
            run.id,
            "agent_approvals_aggregated",
            {"group_count": len(groups), "approval_required": pending},
        )
        agent_observability.observe(
            "approval_decided",
            task_id=task.id,
            run_id=run.id,
            phase=AgentPhase.AGGREGATE_RISK_AND_APPROVALS.value,
            approval_count=len(groups),
            outcome="waiting" if pending else "not_required",
        )
        await runtime.save_checkpoint(
            run.id,
            phase=AgentPhase.AGGREGATE_RISK_AND_APPROVALS,
            checkpoint_key="agent-risk-aggregation-v1",
            input_hash=aggregation_hash,
            payload={
                "group_count": len(groups),
                "approval_required": pending,
            },
        )
        if pending:
            return AgentWorkResult(next_status=AgentRunStatus.WAITING_HUMAN)
        return AgentWorkResult(next_phase=AgentPhase.COMPILE_EXECUTION_PLAN)

    async def compile(
        self, session: AsyncSession, context: AgentWorkContext
    ) -> AgentWorkResult:
        run, task, source, target, _version, findings = await _finding_inputs(
            session, context.run_id, output_root=self._output_root
        )
        approvals = tuple(
            await session.scalars(
                select(AgentApprovalGroupRecord).where(
                    AgentApprovalGroupRecord.run_id == run.id
                )
            )
        )
        approved = frozenset(item.id for item in approvals if item.status == "approved")
        rejected_findings = {
            UUID(finding_id)
            for group in approvals
            if group.status in {"rejected", "stale"}
            for finding_id in group.finding_ids
        }
        clarifications = tuple(
            await session.scalars(
                select(AgentClarificationRecord).where(
                    AgentClarificationRecord.run_id == run.id
                )
            )
        )
        confirmed = frozenset(
            item.work_item_id for item in clarifications if item.status == "confirmed"
        )
        eligible = tuple(item for item in findings if item.finding_id not in rejected_findings)
        executable = tuple(
            item
            for item in eligible
            if item.operation not in {AgentOperation.RETAIN, AgentOperation.SKIP}
            and (item.kind != "identity_conflict" or item.work_item_id in confirmed)
        )
        runtime = AgentRuntimeRepository(session)
        if not executable:
            await runtime.save_checkpoint(
                run.id,
                phase=AgentPhase.COMPILE_EXECUTION_PLAN,
                checkpoint_key="agent-no-executable-plan-v1",
                input_hash=_hash([str(item.finding_id) for item in eligible]),
                payload={"operation_count": 0},
            )
            return AgentWorkResult(next_phase=AgentPhase.EXECUTE_AND_VERIFY)

        plan = compile_agent_plan(
            eligible,
            approved_group_ids=approved,
            confirmed_conflicts=confirmed,
        )
        operations = [_operation_payload(operation) for operation in plan.operations]
        content_hash = _hash(operations)
        await AgentGovernanceRepository(session).save_plan(
            run=run,
            task=task,
            source_snapshot_id=source.id,
            target_snapshot_id=target.id,
            target_version=plan.target_version,
            finding_ids=tuple(item.finding_id for item in eligible),
            operations=operations,
            content_hash=content_hash,
            compiled_by="agent-governance-v1",
        )
        await runtime.append_event(
            run.id, "agent_plan_compiled", {"operation_count": len(operations)}
        )
        return AgentWorkResult(next_phase=AgentPhase.EXECUTE_AND_VERIFY)

    async def execute(
        self, session: AsyncSession, context: AgentWorkContext
    ) -> AgentWorkResult:
        plan = await session.scalar(
            select(AgentGovernancePlanRecord)
            .where(AgentGovernancePlanRecord.run_id == context.run_id)
            .order_by(AgentGovernancePlanRecord.created_at.desc())
        )
        if plan is None:
            return AgentWorkResult(next_phase=AgentPhase.GENERATE_REPORT)
        parent = await ExecutionRepository(session).current_target_version(context.task_id)
        if parent is None or f"sha256:{parent.file_sha256}" != plan.target_version:
            raise ValueError("Agent CSV target version is stale")
        operations = tuple(
            _operation_from_payload(item, plan.target_version) for item in plan.operations
        )
        versioner = CsvTargetVersioner(
            repository=AgentTargetVersionRepository(ExecutionRepository(session)),
            output_root=self._output_root,
        )
        result = await AgentExecutionService().execute(
            plan_id=plan.id,
            target_version=plan.target_version,
            operations=operations,
            target=CsvAgentTargetAdapter(versioner=versioner, parent=parent),
            outcome_sink=AgentGovernanceRepository(session),
        )
        plan.status = result.status
        output = result.output_target_version
        payload: dict[str, Any] = {"status": result.status}
        if isinstance(output, TargetVersionRecord):
            payload.update(
                {
                    "output_target_version_id": str(output.id),
                    "output_target_path": output.storage_path,
                }
            )
        await AgentRuntimeRepository(session).save_checkpoint(
            context.run_id,
            phase=AgentPhase.EXECUTE_AND_VERIFY,
            checkpoint_key="agent-csv-execution-v1",
            input_hash=plan.content_hash,
            payload=payload,
        )
        agent_observability.observe(
            "mutation_completed",
            task_id=context.task_id,
            run_id=context.run_id,
            phase=AgentPhase.EXECUTE_AND_VERIFY.value,
            mutation_count=len(result.by_operation),
            outcome=result.status,
        )
        return AgentWorkResult(next_phase=AgentPhase.GENERATE_REPORT)

    async def execute_operation(
        self,
        session: AsyncSession,
        context: AgentWorkContext,
        *,
        operation_id: UUID,
    ) -> AgentGovernanceOperationRecord:
        plan = await session.scalar(
            select(AgentGovernancePlanRecord)
            .where(AgentGovernancePlanRecord.run_id == context.run_id)
            .order_by(AgentGovernancePlanRecord.created_at.desc())
        )
        if plan is None:
            raise LookupError("Agent governance plan is missing")
        record = await session.scalar(
            select(AgentGovernanceOperationRecord)
            .where(
                AgentGovernanceOperationRecord.id == operation_id,
                AgentGovernanceOperationRecord.plan_id == plan.id,
                AgentGovernanceOperationRecord.run_id == context.run_id,
            )
            .with_for_update()
        )
        if record is None:
            raise LookupError("Agent governance operation is missing")
        if record.status in {
            "succeeded",
            "failed",
            "blocked",
            "verification_failed",
        }:
            return record
        dependency_ids = tuple(UUID(value) for value in record.dependencies)
        if dependency_ids:
            dependencies = tuple(
                await session.scalars(
                    select(AgentGovernanceOperationRecord).where(
                        AgentGovernanceOperationRecord.id.in_(dependency_ids),
                        AgentGovernanceOperationRecord.plan_id == plan.id,
                    )
                )
            )
            by_id = {item.id: item for item in dependencies}
            if set(by_id) != set(dependency_ids):
                raise ValueError("Agent operation dependency is missing")
            incomplete = tuple(
                item for item in dependencies if item.status != "succeeded"
            )
            if incomplete:
                if any(item.status in {"pending", "running"} for item in incomplete):
                    raise ValueError("Agent operation dependency is not ready")
                return await AgentGovernanceRepository(
                    session
                ).record_operation_outcome(
                    record.id,
                    status="blocked",
                    attempts=0,
                    error_code="dependency_failed",
                )
        executions = ExecutionRepository(session)
        parent = await executions.current_target_version(context.task_id)
        if parent is None:
            raise LookupError("Agent CSV target version is missing")
        succeeded = tuple(
            await session.scalars(
                select(AgentGovernanceOperationRecord).where(
                    AgentGovernanceOperationRecord.plan_id == plan.id,
                    AgentGovernanceOperationRecord.status == "succeeded",
                )
            )
        )
        if not succeeded:
            if f"sha256:{parent.file_sha256}" != plan.target_version:
                raise ValueError("Agent CSV target version is stale")
        else:
            output_version_ids = {
                UUID(str(item.verification["output_target_version_id"]))
                for item in succeeded
                if item.verification
                and item.verification.get("output_target_version_id")
            }
            if parent.id not in output_version_ids:
                raise ValueError("Agent CSV target version changed outside the plan")
        payload = next(
            (
                item
                for item in plan.operations
                if UUID(str(item["id"])) == operation_id
            ),
            None,
        )
        if payload is None:
            raise LookupError("Agent operation payload is missing")
        target_version = f"sha256:{parent.file_sha256}"
        operation = replace(
            _operation_from_payload(payload, target_version),
            dependencies=frozenset(),
        )
        result = await AgentExecutionService().execute(
            plan_id=operation.id,
            target_version=target_version,
            operations=(operation,),
            target=CsvAgentTargetAdapter(
                versioner=CsvTargetVersioner(
                    repository=AgentTargetVersionRepository(executions),
                    output_root=self._output_root,
                ),
                parent=parent,
            ),
            outcome_sink=AgentGovernanceRepository(session),
        )
        stored = await session.get(AgentGovernanceOperationRecord, operation_id)
        if stored is None:
            raise LookupError("Agent operation outcome is missing")
        output = result.output_target_version
        verification = dict(stored.verification or {})
        if isinstance(output, TargetVersionRecord):
            verification.update(
                {
                    "output_target_version_id": str(output.id),
                    "output_target_version": f"sha256:{output.file_sha256}",
                }
            )
        stored.verification = verification
        if stored.status != "succeeded":
            blocked_dependency_ids = {stored.id}
            pending = tuple(
                await session.scalars(
                    select(AgentGovernanceOperationRecord).where(
                        AgentGovernanceOperationRecord.plan_id == plan.id,
                        AgentGovernanceOperationRecord.status == "pending",
                    )
                )
            )
            changed = True
            while changed:
                changed = False
                for dependent in pending:
                    if dependent.id in blocked_dependency_ids:
                        continue
                    dependent_ids = {
                        UUID(str(value)) for value in dependent.dependencies
                    }
                    if dependent_ids.intersection(blocked_dependency_ids):
                        await AgentGovernanceRepository(
                            session
                        ).record_operation_outcome(
                            dependent.id,
                            status="blocked",
                            attempts=0,
                            error_code="dependency_failed",
                        )
                        blocked_dependency_ids.add(dependent.id)
                        changed = True
        statuses = tuple(
            await session.scalars(
                select(AgentGovernanceOperationRecord.status).where(
                    AgentGovernanceOperationRecord.plan_id == plan.id
                )
            )
        )
        if any(status in {"pending", "running"} for status in statuses):
            plan.status = "executing"
        elif statuses and all(status == "succeeded" for status in statuses):
            plan.status = "succeeded"
        elif any(status == "succeeded" for status in statuses):
            plan.status = "partial"
        else:
            plan.status = "failed"
        await AgentRuntimeRepository(session).save_checkpoint(
            context.run_id,
            phase=AgentPhase.EXECUTE_AND_VERIFY,
            checkpoint_key=f"agent-csv-operation-v1:{operation_id}",
            input_hash=_hash(payload),
            payload={
                "operation_id": str(operation_id),
                "status": stored.status,
                "output_target_version_id": (
                    str(output.id)
                    if isinstance(output, TargetVersionRecord)
                    else None
                ),
            },
        )
        return stored

    async def report(
        self, session: AsyncSession, context: AgentWorkContext
    ) -> AgentWorkResult:
        run = await session.get(AgentRunRecord, context.run_id)
        task = await session.get(ReconciliationTask, context.task_id)
        if run is None or task is None:
            raise LookupError("Agent reporting context is missing")
        facts = await build_agent_report_facts(session, run_id=run.id)
        if self._settings is not None:
            output_version_id = facts.get("output_target_version_id")
            facts["publication"] = await publish_local_target(
                session,
                settings=self._settings,
                task_id=task.id,
                run_id=run.id,
                phase=AgentPhase.GENERATE_REPORT,
                target_version_id=(
                    UUID(str(output_version_id))
                    if output_version_id is not None
                    else None
                ),
            )
        operations = tuple(
            await session.scalars(
                select(AgentGovernanceOperationRecord).where(
                    AgentGovernanceOperationRecord.run_id == run.id
                )
            )
        )
        report = await AgentReportingService(session).generate(
            task_id=task.id,
            tenant_id=task.tenant_id,
            kind=run.kind,
            terminal_state="completed",
            facts=facts,
        )
        task.status = "completed"
        task.stage = "terminal"
        await AgentRuntimeRepository(session).append_event(
            run.id,
            "report_ready",
            {"report_id": str(report.id), "terminal_state": report.terminal_state},
        )
        agent_observability.observe(
            "report_completed",
            task_id=task.id,
            run_id=run.id,
            phase=AgentPhase.GENERATE_REPORT.value,
            mutation_count=len(operations),
            outcome=report.terminal_state,
        )
        return AgentWorkResult(next_phase=AgentPhase.TERMINAL)


async def build_agent_report_facts(
    session: AsyncSession,
    *,
    run_id: UUID,
) -> dict[str, Any]:
    """Build server-owned report facts without generating model narrative."""

    findings = tuple(
        await session.scalars(
            select(AgentFindingRecord).where(AgentFindingRecord.run_id == run_id)
        )
    )
    marks = tuple(
        await session.scalars(
            select(AgentInputMarkRecord)
            .join(AgentInputRecord)
            .where(AgentInputRecord.run_id == run_id)
        )
    )
    operations = tuple(
        await session.scalars(
            select(AgentGovernanceOperationRecord).where(
                AgentGovernanceOperationRecord.run_id == run_id
            )
        )
    )
    checkpoint = await AgentRuntimeRepository(session).get_checkpoint(
        run_id,
        phase=AgentPhase.EXECUTE_AND_VERIFY,
        checkpoint_key="agent-csv-execution-v1",
    )
    facts: dict[str, Any] = {
        "findings": [
            {"id": str(item.id), "kind": item.kind, "category_zh": item.category_zh}
            for item in findings
        ],
        "excluded_findings": [
            {"reason": item.reason_code, "disposition": item.report_disposition}
            for item in marks
        ],
        "mutations": [
            {
                "id": str(item.id),
                "status": item.status,
                "operation": item.operation_type,
                "entity_kind": item.entity_kind,
                "target_source_identifier": item.target_source_identifier,
                "before": item.before,
                "after": item.actual_after,
                "verification": item.verification
                or {"valid": item.status == "succeeded"},
            }
            for item in operations
        ],
    }
    output_target_version_ids = tuple(
        str(item.verification["output_target_version_id"])
        for item in operations
        if item.status == "succeeded"
        and item.verification
        and item.verification.get("output_target_version_id")
    )
    if output_target_version_ids:
        facts["output_target_version_ids"] = list(output_target_version_ids)
        facts["output_target_version_id"] = output_target_version_ids[-1]
    if checkpoint is not None:
        facts.update(checkpoint.payload)
    return facts


async def _finding_inputs(
    session: AsyncSession,
    run_id: UUID,
    *,
    output_root: Path,
) -> tuple[
    AgentRunRecord,
    ReconciliationTask,
    Snapshot,
    Snapshot,
    TargetVersionRecord,
    tuple[AgentFindingInput, ...],
]:
    run = await session.get(AgentRunRecord, run_id)
    if run is None:
        raise LookupError("Agent run not found")
    task = await session.get(ReconciliationTask, run.task_id)
    if task is None:
        raise LookupError("Agent task not found")
    snapshots = tuple(await session.scalars(select(Snapshot).where(Snapshot.task_id == task.id)))
    by_role = {item.source_role: item for item in snapshots}
    source, target = by_role["authoritative"], by_role["target"]
    executions = ExecutionRepository(session)
    version = await executions.current_target_version(task.id)
    if version is None:
        target_file = await session.get(SourceFile, target.source_file_id)
        if target_file is None:
            raise LookupError("Agent target CSV is missing")
        storage_path = Path(target_file.storage_path)
        target_intent = (task.agent_intent or {}).get("target", {})
        if isinstance(target_intent, dict) and target_intent.get("kind") == "local":
            managed = copy_managed_initial_version(
                storage_path,
                output_root=output_root / "initial",
                task_id=task.id,
                expected_sha256=target.file_hash,
            )
            storage_path = managed.path
        version = await executions.create_target_version(
            task_id=task.id,
            tenant_id=task.tenant_id,
            source_snapshot_id=target.id,
            parent_version_id=None,
            batch_id=None,
            file_sha256=target.file_hash,
            content_hash=target.content_hash,
            storage_path=storage_path,
        )
    rows = tuple(
        await session.execute(
            select(
                AgentFindingRecord,
                AgentWorkItemRecord,
                AgentInputRecord,
                AgentFindingSolutionRecord,
            )
            .join(AgentWorkItemRecord, AgentWorkItemRecord.id == AgentFindingRecord.work_item_id)
            .join(AgentInputRecord, AgentInputRecord.id == AgentWorkItemRecord.subject_input_id)
            .join(
                AgentFindingSolutionRecord,
                (AgentFindingSolutionRecord.finding_id == AgentFindingRecord.id)
                & AgentFindingSolutionRecord.recommended.is_(True),
            )
            .where(AgentFindingRecord.run_id == run.id)
            .order_by(AgentFindingRecord.id)
        )
    )
    inputs: list[AgentFindingInput] = []
    for finding, work, subject, solution in rows:
        before: dict[str, object] | None = None
        after: dict[str, object] | None = None
        target_identifier: str | None = None
        if work.kind == "field_difference":
            claim = await session.scalar(
                select(AgentIdentityClaimRecord).where(
                    AgentIdentityClaimRecord.work_item_id == work.id
                )
            )
            if claim is None:
                raise ValueError("field difference has no identity claim")
            authority = await session.get(AgentInputRecord, claim.authority_input_id)
            target_input = await session.get(AgentInputRecord, claim.target_input_id)
            if authority is None or target_input is None:
                raise ValueError("identity claim inputs are missing")
            before, after = _changed_values(target_input, authority)
            target_identifier = target_input.stable_locator
        elif work.kind == "target_missing":
            after = _record_values(subject)
            after["source_id"] = subject.number or subject.email or str(subject.id)
        elif work.kind in {"target_extra", "target_duplicate"}:
            before = _record_values(subject)
            target_identifier = subject.stable_locator
        dependencies = frozenset(
            await session.scalars(
                select(AgentFindingDependencyRecord.depends_on_finding_id).where(
                    AgentFindingDependencyRecord.finding_id == finding.id
                )
            )
        )
        inputs.append(
            AgentFindingInput(
                finding_id=finding.id,
                work_item_id=work.id,
                entity_kind=work.entity_kind,
                kind=work.kind,
                operation=AgentOperation(solution.operation),
                changed_fields=frozenset(set(before or {}) | set(after or {})),
                before=before,
                after=after,
                target_source_identifier=target_identifier,
                dependencies=dependencies,
                analysis_terminal=True,
                target_version=f"sha256:{version.file_sha256}",
            )
        )
    return run, task, source, target, version, tuple(inputs)


def _record_values(record: AgentInputRecord) -> dict[str, object]:
    values: dict[str, object] = {
        "category": record.category,
        "name": record.name,
        "number": record.number,
        "phone": record.phone,
        "email": record.email,
    }
    if record.entity_kind == "student":
        values["class_name"] = record.class_name
    return values


def _changed_values(
    target: AgentInputRecord, authority: AgentInputRecord
) -> tuple[dict[str, object], dict[str, object]]:
    target_values = _record_values(target)
    authority_values = _record_values(authority)
    fields = tuple(
        key for key in authority_values if authority_values.get(key) != target_values.get(key)
    )
    return (
        {key: target_values[key] for key in fields},
        {key: authority_values[key] for key in fields},
    )


def _operation_payload(operation: AgentGovernanceOperation) -> dict[str, Any]:
    return {
        "id": str(operation.id),
        "finding_id": str(operation.finding_id),
        "operation": str(operation.operation),
        "entity_kind": operation.entity_kind,
        "target_source_identifier": operation.target_source_identifier,
        "before": dict(operation.before) if operation.before is not None else None,
        "after": dict(operation.after) if operation.after is not None else None,
        "dependencies": [str(item) for item in sorted(operation.dependencies, key=str)],
        "risk": operation.risk,
    }


def _operation_from_payload(
    payload: dict[str, Any], target_version: str
) -> AgentGovernanceOperation:
    return AgentGovernanceOperation(
        id=UUID(payload["id"]),
        finding_id=UUID(payload["finding_id"]),
        operation=AgentOperation(payload["operation"]),
        entity_kind=payload["entity_kind"],
        target_source_identifier=payload.get("target_source_identifier"),
        before=payload.get("before"),
        after=payload.get("after"),
        dependencies=frozenset(UUID(item) for item in payload.get("dependencies", ())),
        risk=payload["risk"],
        target_version=target_version,
    )


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
