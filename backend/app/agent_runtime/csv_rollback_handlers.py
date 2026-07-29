"""Independent, school-exclusive rollback handlers for verified Agent CSV facts."""

import asyncio
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_reporting.service import (
    AgentReportingService,
    rollback_terminal_state,
)
from app.agent_runtime.csv_governance_handlers import AgentTargetVersionRepository
from app.agent_runtime.local_publication import publish_local_target
from app.agent_runtime.observability import agent_observability
from app.agent_runtime.repository import AgentRuntimeRepository
from app.agent_runtime.state_machine import AgentPhase
from app.agent_runtime.worker import AgentWorkContext, AgentWorkResult
from app.core.config import Settings
from app.executions.agent_service import AgentExecutionService, CsvAgentTargetAdapter
from app.executions.csv_versioning import (
    _AGENT_COLUMN_ALIASES,
    _COLUMN_NAMES,
    CsvMutationError,
    CsvTargetVersioner,
    read_target_fieldnames,
    read_target_rows,
)
from app.governance.agent_governance import AgentGovernanceOperation, AgentOperation
from app.local_sources.publisher import copy_managed_initial_version
from app.local_sources.service import LocalSourceService
from app.models.executions import TargetVersionRecord
from app.models.reconciliation import ReconciliationTask
from app.repositories.executions import ExecutionRepository


class CsvRollbackHandlers:
    def __init__(self, *, output_root: Path, settings: Settings | None = None) -> None:
        self._output_root = output_root
        self._settings = settings

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
        if current is None:
            raise LookupError("current rollback target version is missing")
        current = await self._ensure_live_comparison_version(
            session,
            task=task,
            current=current,
        )
        try:
            current_path = Path(current.storage_path)
            current_rows = read_target_rows(current_path)
            complete_record_fields = set(
                read_target_fieldnames(current_path)
            )
            restore_comparisons = [
                compare_csv_rollback_mutation(
                    mutation,
                    current=current_rows.get(
                        _mutation_target_identifier(mutation)
                    ),
                    complete_record_fields=complete_record_fields,
                )
                for mutation in operations
            ]
        except (CsvMutationError, OSError):
            restore_comparisons = [
                _unavailable_comparison(mutation) for mutation in operations
            ]
        updated_intent = dict(task.agent_intent)
        updated_intent["restore_comparisons"] = restore_comparisons
        updated_intent["comparison_target_version_id"] = str(current.id)
        task.agent_intent = updated_intent
        await AgentRuntimeRepository(session).save_checkpoint(
            context.run_id,
            phase=AgentPhase.PLAN_RESTORE,
            checkpoint_key="agent-csv-rollback-plan-v1",
            input_hash=str(task.request_hash),
            payload={
                "source_task_id": str(task.parent_task_id),
                "target_version_id": str(target.id),
                "comparison_target_version_id": str(current.id),
                "operations": operations,
                "restore_comparisons": restore_comparisons,
            },
        )
        return AgentWorkResult(next_phase=AgentPhase.CLARIFY_RESTORE_CONFLICTS)

    async def _ensure_live_comparison_version(
        self,
        session: AsyncSession,
        *,
        task: ReconciliationTask,
        current: TargetVersionRecord,
    ) -> TargetVersionRecord:
        target_intent = (task.agent_intent or {}).get("target", {})
        if (
            self._settings is None
            or not isinstance(target_intent, dict)
            or target_intent.get("kind") != "local"
        ):
            return current
        source_ref = target_intent.get("source_ref")
        if not isinstance(source_ref, str):
            raise LookupError("rollback local target reference is missing")
        material = LocalSourceService(
            self._settings
        ).describe_target_for_write(source_ref)
        current_path = Path(current.storage_path)
        if await asyncio.to_thread(
            _matches_version_file,
            current_path,
            material.sha256,
        ):
            return current

        managed = copy_managed_initial_version(
            material.path,
            output_root=self._output_root / "rollback-baselines",
            task_id=task.id,
            expected_sha256=material.sha256,
        )
        rows = read_target_rows(managed.path)
        content_hash = hashlib.sha256(
            json.dumps(
                rows,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return await ExecutionRepository(session).create_target_version(
            task_id=current.task_id,
            tenant_id=current.tenant_id,
            source_snapshot_id=current.source_snapshot_id,
            parent_version_id=current.id,
            batch_id=None,
            file_sha256=managed.sha256,
            content_hash=content_hash,
            storage_path=managed.path,
        )

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
        operations = _rollback_operations(
            tuple(task.agent_intent.get("operations", [])),
            target_version=f"sha256:{parent.file_sha256}",
        )
        if not operations:
            raise ValueError("rollback has no verified operations")
        mutations = [
            await self.execute_operation(session, context, operation.id)
            for operation in operations
        ]
        facts: dict[str, Any] = {
            "source_task_id": str(task.parent_task_id),
            "mutations": mutations,
        }
        output_fact = next(
            (
                item
                for item in reversed(mutations)
                if item.get("output_target_version_id")
            ),
            None,
        )
        if output_fact is not None:
            facts.update(
                {
                    "output_target_version_id": output_fact[
                        "output_target_version_id"
                    ],
                    "output_target_path": output_fact["output_target_path"],
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

    async def execute_operation(
        self,
        session: AsyncSession,
        context: AgentWorkContext,
        operation_id: UUID,
    ) -> dict[str, Any]:
        runtime = AgentRuntimeRepository(session)
        checkpoint_key = f"agent-csv-rollback-operation:{operation_id}"
        existing = await runtime.get_checkpoint(
            context.run_id,
            phase=AgentPhase.EXECUTE_RESTORE,
            checkpoint_key=checkpoint_key,
        )
        if existing is not None:
            return dict(existing.payload)

        task = await session.get(ReconciliationTask, context.task_id)
        if task is None or not task.agent_intent:
            raise LookupError("rollback task facts are missing")
        initial_parent = await session.get(
            TargetVersionRecord,
            UUID(str(task.agent_intent["target_version_id"])),
        )
        if initial_parent is None:
            raise LookupError("rollback target version is missing")
        mutation_facts = tuple(task.agent_intent.get("operations", []))
        frozen_operations = _rollback_operations(
            mutation_facts,
            target_version=f"sha256:{initial_parent.file_sha256}",
        )
        operation_by_id = {
            operation.id: operation for operation in frozen_operations
        }
        selected_template = operation_by_id.get(operation_id)
        if selected_template is None:
            raise ValueError("rollback operation is outside the frozen plan")
        mutation_by_operation_id = {
            _rollback_operation(
                mutation,
                target_version=f"sha256:{initial_parent.file_sha256}",
            ).id: dict(mutation)
            for mutation in mutation_facts
        }
        for dependency_id in selected_template.dependencies:
            dependency = await runtime.get_checkpoint(
                context.run_id,
                phase=AgentPhase.EXECUTE_RESTORE,
                checkpoint_key=(
                    f"agent-csv-rollback-operation:{dependency_id}"
                ),
            )
            if dependency is None:
                raise ValueError("rollback operation dependency is not ready")
            if dependency.payload.get("status") not in {
                "succeeded",
                "already_restored",
            }:
                dependency_fact = {
                    "id": str(operation_id),
                    "status": "blocked",
                    "verification": {"valid": False},
                    "compensation_for": str(
                        selected_template.finding_id
                    ),
                    "safe_error_code": "rollback_dependency_failed",
                }
                await runtime.save_checkpoint(
                    context.run_id,
                    phase=AgentPhase.EXECUTE_RESTORE,
                    checkpoint_key=checkpoint_key,
                    input_hash=str(task.request_hash),
                    payload=dependency_fact,
                )
                return dependency_fact

        parent = await ExecutionRepository(session).current_target_version(
            initial_parent.task_id
        )
        selected_mutation = mutation_by_operation_id[operation_id]
        planned_comparison = next(
            (
                dict(item)
                for item in task.agent_intent.get(
                    "restore_comparisons",
                    [],
                )
                if str(item.get("operation_id"))
                == str(selected_mutation["id"])
            ),
            None,
        )
        current_comparison: dict[str, object] | None = None
        if parent is not None:
            try:
                current_path = Path(parent.storage_path)
                current_rows = read_target_rows(current_path)
                current_comparison = compare_csv_rollback_mutation(
                    selected_mutation,
                    current=current_rows.get(
                        _mutation_target_identifier(selected_mutation)
                    ),
                    complete_record_fields=set(
                        read_target_fieldnames(current_path)
                    ),
                )
            except (CsvMutationError, OSError):
                current_comparison = None
        if (
            planned_comparison is None
            or current_comparison is None
            or parent is None
        ):
            no_write_fact = _rollback_no_write_fact(
                selected_template,
                status="conflict_skipped",
                comparison=current_comparison,
                safe_error_code="rollback_comparison_fact_missing",
            )
            await runtime.save_checkpoint(
                context.run_id,
                phase=AgentPhase.EXECUTE_RESTORE,
                checkpoint_key=checkpoint_key,
                input_hash=str(task.request_hash),
                payload=no_write_fact,
            )
            return no_write_fact

        current_disposition = str(current_comparison["disposition"])
        planned_disposition = str(planned_comparison["disposition"])
        if current_disposition == "already_restored":
            no_write_fact = _rollback_no_write_fact(
                selected_template,
                status="already_restored",
                comparison=current_comparison,
            )
            await runtime.save_checkpoint(
                context.run_id,
                phase=AgentPhase.EXECUTE_RESTORE,
                checkpoint_key=checkpoint_key,
                input_hash=str(task.request_hash),
                payload=no_write_fact,
            )
            return no_write_fact
        if not (
            planned_disposition == "safe_to_restore"
            and current_disposition == "safe_to_restore"
            and current_comparison["comparison_hash"]
            == planned_comparison.get("comparison_hash")
        ):
            no_write_fact = _rollback_no_write_fact(
                selected_template,
                status="conflict_skipped",
                comparison=current_comparison,
                safe_error_code="rollback_current_data_conflict",
            )
            await runtime.save_checkpoint(
                context.run_id,
                phase=AgentPhase.EXECUTE_RESTORE,
                checkpoint_key=checkpoint_key,
                input_hash=str(task.request_hash),
                payload=no_write_fact,
            )
            return no_write_fact

        selected = _rollback_operation(
            selected_mutation,
            target_version=f"sha256:{parent.file_sha256}",
        )
        versioner = CsvTargetVersioner(
            repository=AgentTargetVersionRepository(ExecutionRepository(session)),
            output_root=self._output_root,
        )
        result = await AgentExecutionService().execute(
            plan_id=uuid5(
                NAMESPACE_URL,
                f"agent-rollback:{task.id}:operation:{selected.id}",
            ),
            target_version=f"sha256:{parent.file_sha256}",
            operations=(selected,),
            target=CsvAgentTargetAdapter(versioner=versioner, parent=parent),
        )
        operation_result = result.by_operation[selected.id]
        fact: dict[str, Any] = {
            "id": str(operation_result.operation_id),
            "status": operation_result.status,
            "verification": {
                "valid": operation_result.status == "succeeded"
            },
            "compensation_for": str(selected.finding_id),
        }
        output = result.output_target_version
        if isinstance(output, TargetVersionRecord):
            fact.update(
                {
                    "output_target_version_id": str(output.id),
                    "output_target_path": output.storage_path,
                }
            )
        await runtime.save_checkpoint(
            context.run_id,
            phase=AgentPhase.EXECUTE_RESTORE,
            checkpoint_key=checkpoint_key,
            input_hash=str(task.request_hash),
            payload=fact,
        )
        return fact

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
        facts = dict(checkpoint.payload) if checkpoint is not None else {"mutations": []}
        if self._settings is not None:
            output_version_id = facts.get("output_target_version_id")
            facts["publication"] = await publish_local_target(
                session,
                settings=self._settings,
                task_id=task.id,
                run_id=context.run_id,
                phase=AgentPhase.REPORT_RESTORE,
                target_version_id=(
                    UUID(str(output_version_id))
                    if output_version_id is not None
                    else None
                ),
            )
        terminal_state = rollback_terminal_state(facts)
        report = await AgentReportingService(session).generate(
            task_id=task.id,
            tenant_id=task.tenant_id,
            kind="rollback",
            terminal_state=terminal_state,
            facts=facts,
        )
        task.status = "completed"
        task.stage = "terminal"
        await AgentRuntimeRepository(session).append_event(
            context.run_id,
            "report_ready",
            {"report_id": str(report.id), "terminal_state": terminal_state},
        )
        agent_observability.observe(
            "rollback_completed",
            task_id=task.id,
            run_id=context.run_id,
            phase=AgentPhase.REPORT_RESTORE.value,
            mutation_count=len(facts.get("mutations", [])),
            outcome=terminal_state,
        )
        return AgentWorkResult(next_phase=AgentPhase.TERMINAL)


_FULL_RECORD_FIELDS = frozenset(
    {
        "source_id",
        "category",
        "name",
        "number",
        "class_name",
        "phone",
        "email",
    }
)
_PROJECTED_CSV_RECORD_FIELDS = frozenset(
    {
        *_FULL_RECORD_FIELDS,
        *_COLUMN_NAMES,
        *_COLUMN_NAMES.values(),
        *(
            alias
            for aliases in _AGENT_COLUMN_ALIASES.values()
            for alias in aliases
        ),
    }
)


def _rollback_no_write_fact(
    operation: AgentGovernanceOperation,
    *,
    status: str,
    comparison: dict[str, object] | None,
    safe_error_code: str | None = None,
) -> dict[str, object]:
    fact: dict[str, object] = {
        "id": str(operation.id),
        "status": status,
        "verification": {
            "valid": status == "already_restored",
            "no_write": True,
            "comparison_hash": (
                comparison.get("comparison_hash")
                if comparison is not None
                else None
            ),
            "disposition": (
                comparison.get("disposition")
                if comparison is not None
                else "unavailable"
            ),
        },
        "compensation_for": str(operation.finding_id),
    }
    if safe_error_code is not None:
        fact["safe_error_code"] = safe_error_code
    return fact


def compare_csv_rollback_mutation(
    mutation: dict[str, Any],
    *,
    current: dict[str, object] | None,
    complete_record_fields: set[str] | None = None,
) -> dict[str, object]:
    """Classify one verified mutation from its affected current target values."""
    operation_id = str(mutation["id"])
    operation = AgentOperation(str(mutation["operation"]))
    before = _fact_mapping(mutation.get("before"))
    after = _fact_mapping(mutation.get("after"))
    identifier = _mutation_target_identifier(mutation)

    if not identifier:
        fields = []
        disposition = "conflict"
        reason_code = "target_identifier_missing"
    elif operation == AgentOperation.UPDATE:
        fields = sorted(
            (set(before) | set(after)) - {"source_id", "entity_type"}
        )
        if not fields or current is None:
            disposition = "conflict"
            reason_code = (
                "mutation_values_missing"
                if not fields
                else "target_record_missing"
            )
        elif _fields_match(current, before, fields):
            disposition = "already_restored"
            reason_code = "current_matches_before"
        elif _fields_match(current, after, fields):
            disposition = "safe_to_restore"
            reason_code = "current_matches_after"
        else:
            disposition = "conflict"
            reason_code = "affected_fields_changed"
    elif operation == AgentOperation.CREATE:
        custom_current_fields = (
            set(current) - _PROJECTED_CSV_RECORD_FIELDS
            if current is not None
            else set()
        )
        custom_schema_fields = (
            (complete_record_fields or set())
            - _PROJECTED_CSV_RECORD_FIELDS
        )
        fields = sorted(
            _FULL_RECORD_FIELDS
            | set(after)
            | custom_current_fields
            | custom_schema_fields
        )
        if current is None:
            disposition = "already_restored"
            reason_code = "created_record_absent"
        elif not after:
            disposition = "conflict"
            reason_code = "mutation_values_missing"
        elif _fields_match(
            current,
            _complete_record(
                after,
                identifier=identifier,
                fields=fields,
            ),
            fields,
        ):
            disposition = "safe_to_restore"
            reason_code = "current_matches_after"
        else:
            disposition = "conflict"
            reason_code = "created_record_changed"
    else:
        fields = sorted(
            _FULL_RECORD_FIELDS
            | set(before)
            | (complete_record_fields or set())
        )
        if current is None:
            missing_complete_fields = (
                (complete_record_fields or set()) - set(before)
            )
            if missing_complete_fields:
                disposition = "conflict"
                reason_code = "complete_record_fact_missing"
            else:
                disposition = "safe_to_restore"
                reason_code = "deleted_record_still_absent"
        elif not before:
            disposition = "conflict"
            reason_code = "mutation_values_missing"
        elif _fields_match(
            current,
            _complete_record(
                before,
                identifier=identifier,
                fields=fields,
            ),
            fields,
        ):
            disposition = "already_restored"
            reason_code = "current_matches_before"
        else:
            disposition = "conflict"
            reason_code = "deleted_record_replacement_changed"

    comparison_material = {
        "operation_id": operation_id,
        "operation": str(operation),
        "affected_fields": fields,
        "current": {
            "present": current is not None,
            "values": {
                field: _csv_fact_value(current.get(field))
                for field in fields
            }
            if current is not None
            else {},
        },
    }
    return {
        "operation_id": operation_id,
        "disposition": disposition,
        "reason_code": reason_code,
        "affected_fields": fields,
        "comparison_hash": hashlib.sha256(
            json.dumps(
                comparison_material,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
    }


def _unavailable_comparison(mutation: dict[str, Any]) -> dict[str, object]:
    operation_id = str(mutation["id"])
    material = f"{operation_id}:current_target_unavailable"
    return {
        "operation_id": operation_id,
        "disposition": "conflict",
        "reason_code": "current_target_unavailable",
        "affected_fields": [],
        "comparison_hash": hashlib.sha256(material.encode()).hexdigest(),
    }


def _mutation_target_identifier(mutation: dict[str, Any]) -> str:
    identifier = mutation.get("target_source_identifier")
    after = _fact_mapping(mutation.get("after"))
    resolved = identifier or after.get("source_id")
    return str(resolved or "")


def _fact_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {str(field): item for field, item in value.items()}


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _matches_version_file(path: Path, expected_sha256: str) -> bool:
    return (
        path.is_file()
        and not path.is_symlink()
        and _file_sha256(path) == expected_sha256
    )


def _complete_record(
    values: dict[str, object],
    *,
    identifier: str,
    fields: list[str],
) -> dict[str, object]:
    completed: dict[str, object] = {field: "" for field in fields}
    completed.update(values)
    completed["source_id"] = identifier
    return completed


def _fields_match(
    current: dict[str, object],
    expected: dict[str, object],
    fields: list[str],
) -> bool:
    return all(
        _csv_fact_value(current.get(field))
        == _csv_fact_value(expected.get(field))
        for field in fields
    )


def _csv_fact_value(value: object) -> str:
    return "" if value is None else str(value)


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


def _rollback_operations(
    mutations: tuple[dict[str, Any], ...],
    *,
    target_version: str,
) -> tuple[AgentGovernanceOperation, ...]:
    base_by_original_id = {
        UUID(str(mutation["id"])): _rollback_operation(
            mutation,
            target_version=target_version,
        )
        for mutation in mutations
    }
    reverse_dependencies: dict[UUID, set[UUID]] = {
        operation_id: set() for operation_id in base_by_original_id
    }
    for mutation in mutations:
        dependent_id = UUID(str(mutation["id"]))
        for dependency in mutation.get("dependencies", []):
            dependency_id = UUID(str(dependency))
            if dependency_id in reverse_dependencies:
                reverse_dependencies[dependency_id].add(dependent_id)

    operations = {
        original_id: replace(
            operation,
            dependencies=frozenset(
                base_by_original_id[dependent_id].id
                for dependent_id in reverse_dependencies[original_id]
            ),
        )
        for original_id, operation in base_by_original_id.items()
    }
    ordered: list[AgentGovernanceOperation] = []
    completed: set[UUID] = set()
    remaining = list(operations.values())
    while remaining:
        ready = [
            operation
            for operation in remaining
            if operation.dependencies.issubset(completed)
        ]
        if not ready:
            raise ValueError("rollback operation dependencies contain a cycle")
        for operation in ready:
            ordered.append(operation)
            completed.add(operation.id)
            remaining.remove(operation)
    return tuple(ordered)
