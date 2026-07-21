import csv
import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers.base import LLMProvider, ModelProviderError
from app.core.security import OperatorContext
from app.executions.executor import ExecutionExecutor
from app.models.executions import TargetVersionRecord
from app.models.snapshots import Snapshot
from app.repositories.executions import ExecutionRepository
from app.repositories.reporting import ReportingRepository
from app.restores.advisor import RestoreAdvisor
from app.restores.planner import HistoricalRestorePlanner
from app.schemas.executions import ExecutionBatchResult, ExecutionBatchStatus, GovernancePlan
from app.schemas.reporting import RestoreConfirmation, RestorePreview


class RestoreService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        operator: OperatorContext,
        provider: LLMProvider,
        tokenization_secret: str | None,
    ) -> None:
        self.session = session
        self.operator = operator
        self.executions = ExecutionRepository(session)
        self.reporting = ReportingRepository(session)
        self.planner = HistoricalRestorePlanner(session)
        self.advisor = RestoreAdvisor(provider, tokenization_secret=tokenization_secret)

    async def versions(self, task_id: UUID) -> tuple[TargetVersionRecord, ...]:
        records = tuple(
            await self.session.scalars(
                select(TargetVersionRecord)
                .where(
                    TargetVersionRecord.task_id == task_id,
                    TargetVersionRecord.tenant_id == self.operator.tenant_id,
                )
                .order_by(TargetVersionRecord.created_at, TargetVersionRecord.id)
            )
        )
        if not records:
            raise LookupError("target versions not found")
        return records

    async def preview(self, target_version_id: UUID) -> RestorePreview:
        target = await self.executions.get_target_version(target_version_id)
        if target is None or target.tenant_id != self.operator.tenant_id:
            raise LookupError("target version not found")
        versions = await self.versions(target.task_id)
        source = versions[-1]
        semantic_source_id = await self.reporting.resolve_semantic_version(
            source.id, tenant_id=self.operator.tenant_id
        )
        semantic_target_id = await self.reporting.resolve_semantic_version(
            target.id, tenant_id=self.operator.tenant_id
        )
        result = await self.planner.plan(
            source_version_id=semantic_source_id,
            target_version_id=semantic_target_id,
            tenant_id=self.operator.tenant_id,
        )
        if not result.allowed:
            payload = {
                "source_version_id": str(source.id),
                "semantic_source_version_id": str(semantic_source_id),
                "target_version_id": str(target.id),
                "allowed": False,
                "conflicts": [item.model_dump(mode="json") for item in result.conflicts],
            }
            digest = _hash(payload)
            request = await self.reporting.create_restore_request(
                task_id=target.task_id,
                tenant_id=self.operator.tenant_id,
                source_version_id=source.id,
                semantic_source_version_id=semantic_source_id,
                target_version_id=target.id,
                preview_hash=digest,
                deterministic_plan=payload,
                covered_execution_ids=result.covered_execution_ids,
                requested_by=self.operator.operator_id,
            )
            return RestorePreview(
                task_id=target.task_id,
                restore_request_id=request.id,
                source_version_id=source.id,
                semantic_source_version_id=semantic_source_id,
                target_version_id=target.id,
                preview_hash=digest,
                allowed=False,
                conflicts=result.conflicts,
                operations=(),
                covered_execution_ids=result.covered_execution_ids,
            )
        authoritative = await self.session.scalar(
            select(Snapshot).where(
                Snapshot.task_id == target.task_id,
                Snapshot.source_role == "authoritative",
            )
        )
        if authoritative is None:
            raise ValueError("authoritative snapshot is missing")
        operations = tuple(item.model_dump(mode="json") for item in result.operations)
        plan_payload = {
            "task_id": str(target.task_id),
            "source_version_id": str(source.id),
            "semantic_source_version_id": str(semantic_source_id),
            "semantic_target_version_id": str(semantic_target_id),
            "target_version_id": str(target.id),
            "operations": operations,
        }
        plan_hash = _hash(plan_payload)
        plan = GovernancePlan(
            id=uuid4(),
            task_id=target.task_id,
            source_snapshot_id=authoritative.id,
            target_snapshot_id=target.source_snapshot_id,
            target_version=source.content_hash,
            proposals=tuple(dict.fromkeys(item.proposal for item in result.operations)),
            operations=result.operations,
            content_hash=plan_hash,
        )
        saved = await self.executions.save_plan(plan, created_by=self.operator.operator_id)
        explanation = None
        explanation_state = "unavailable"
        ai_candidate = None
        ai_provenance = None
        try:
            advice, provenance = await self.advisor.advise(
                result,
                task_id=target.task_id,
                tenant_id=self.operator.tenant_id,
            )
            explanation = advice.explanation
            explanation_state = "available"
            ai_candidate = advice.model_dump(mode="json")
            ai_provenance = provenance
        except (ModelProviderError, ValueError):
            pass
        deterministic = {
            **plan_payload,
            "plan_id": str(saved.id),
            "plan_hash": saved.content_hash,
            "allowed": True,
        }
        preview_hash = _hash(deterministic)
        request = await self.reporting.create_restore_request(
            task_id=target.task_id,
            tenant_id=self.operator.tenant_id,
            source_version_id=source.id,
            semantic_source_version_id=semantic_source_id,
            target_version_id=target.id,
            preview_hash=preview_hash,
            deterministic_plan=deterministic,
            covered_execution_ids=result.covered_execution_ids,
            requested_by=self.operator.operator_id,
            ai_candidate=ai_candidate,
            ai_provenance=ai_provenance,
        )
        return RestorePreview(
            task_id=target.task_id,
            restore_request_id=request.id,
            source_version_id=source.id,
            semantic_source_version_id=semantic_source_id,
            target_version_id=target.id,
            preview_hash=preview_hash,
            allowed=True,
            conflicts=(),
            operations=operations,
            covered_execution_ids=result.covered_execution_ids,
            explanation=explanation,
            explanation_state=explanation_state,
        )

    async def confirm(
        self,
        preview_hash: str,
        *,
        idempotency_key: str,
        high_risk_acknowledged: bool,
    ) -> RestoreConfirmation:
        request = await self.reporting.get_restore_by_preview_hash(preview_hash)
        if request is None or request.tenant_id != self.operator.tenant_id:
            raise LookupError("restore preview not found")
        if not request.deterministic_plan.get("allowed"):
            raise ValueError("restore preview is blocked")
        if not high_risk_acknowledged:
            raise ValueError("high-risk acknowledgement is required")
        link = await self.reporting.get_restore_link(request.id)
        if link is not None:
            batch = await self.executions.get_batch(link.compensation_batch_id)
            if batch is None:
                raise LookupError("restore execution batch not found")
            if batch.idempotency_key != idempotency_key:
                raise ValueError("restore preview was already confirmed")
            return RestoreConfirmation(
                restore_request_id=request.id,
                batch_id=batch.id,
                plan_id=batch.plan_id,
                input_target_version_id=batch.input_target_version_id,
                confirmed_by=batch.confirmed_by,
                status=batch.status,
            )
        versions = await self.versions(request.task_id)
        if versions[-1].id != request.source_version_id:
            raise ValueError("restore preview is stale")
        plan_id = UUID(str(request.deterministic_plan["plan_id"]))
        plan = await self.executions.get_plan(plan_id)
        if plan is None:
            raise LookupError("restore plan not found")
        batch = await self.executions.create_batch(
            plan_id=plan.id,
            plan_version=plan.version,
            input_target_version_id=request.source_version_id,
            idempotency_key=idempotency_key,
            confirmed_by=self.operator.operator_id,
            high_risk_acknowledged=True,
            preflight_result={
                "valid": True,
                "restore_request_id": str(request.id),
                "preview_hash": preview_hash,
            },
        )
        await self.reporting.link_restore_execution(
            restore_request_id=request.id,
            compensation_plan_id=plan.id,
            compensation_batch_id=batch.id,
        )
        return RestoreConfirmation(
            restore_request_id=request.id,
            batch_id=batch.id,
            plan_id=plan.id,
            input_target_version_id=request.source_version_id,
            confirmed_by=batch.confirmed_by,
            status=batch.status,
        )

    async def execute(
        self,
        restore_request_id: UUID,
        *,
        executor: ExecutionExecutor,
    ) -> ExecutionBatchResult:
        request = await self.reporting.get_restore_request(restore_request_id)
        if request is None or request.tenant_id != self.operator.tenant_id:
            raise LookupError("restore request not found")
        link = await self.reporting.get_restore_link(request.id)
        if link is None:
            raise LookupError("restore execution not found")
        versions = await self.versions(request.task_id)
        batch = await self.executions.get_batch(link.compensation_batch_id)
        if batch is None:
            raise LookupError("restore execution batch not found")
        if versions[-1].id != batch.input_target_version_id:
            raise ValueError("restore execution target has drifted")
        result = await executor.execute(batch.id)
        if result.status is not ExecutionBatchStatus.SUCCEEDED:
            return result
        if result.output_target_version_id is None:
            raise ValueError("restore execution did not produce a target version")
        output = await self.executions.get_target_version(result.output_target_version_id)
        target = await self.executions.get_target_version(request.target_version_id)
        if output is None or target is None:
            raise LookupError("restore target version not found")
        output_hash = _csv_state_hash(Path(output.storage_path))
        try:
            target_hash = _verified_csv_state_hash(target)
        except ValueError as error:
            await self.executions.append_audit_event(
                batch_id=batch.id,
                actor_id=batch.confirmed_by,
                event_type="restore_target_integrity_failed",
                details={
                    "output_target_version_id": str(output.id),
                    "selected_target_version_id": str(target.id),
                    "message": str(error),
                },
            )
            return result.model_copy(update={"status": ExecutionBatchStatus.FAILED})
        if output_hash != target_hash:
            await self.executions.append_audit_event(
                batch_id=batch.id,
                actor_id=batch.confirmed_by,
                event_type="restore_content_verification_failed",
                details={
                    "output_target_version_id": str(output.id),
                    "selected_target_version_id": str(target.id),
                    "actual_content_hash": output_hash,
                    "expected_content_hash": target_hash,
                },
            )
            return result.model_copy(update={"status": ExecutionBatchStatus.FAILED})
        await self.reporting.append_restore_result(
            restore_execution_link_id=link.id,
            output_version_id=output.id,
            verified_content_hash=output_hash,
        )
        return result


def _hash(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _csv_state_hash(path: Path) -> str:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = [
            {key: value for key, value in row.items() if value not in {None, ""}}
            for row in csv.DictReader(handle)
        ]
    return _hash(sorted(rows, key=lambda row: (row.get("entity_type", ""), row.get("id", ""))))


def _verified_csv_state_hash(version: TargetVersionRecord) -> str:
    path = Path(version.storage_path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != version.file_sha256:
        raise ValueError("selected historical target file failed integrity validation")
    return _csv_state_hash(path)
