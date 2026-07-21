from collections.abc import Mapping, Sequence
from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.reporting import (
    GovernanceReportRecord,
    ReportJobRecord,
    RestoreExecutionLinkRecord,
    RestoreExecutionResultRecord,
    RestoreRequestRecord,
)
from app.schemas.reporting import RestoreState


class ReportingConflict(ValueError):
    pass


class ReportingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def start_report(
        self,
        *,
        execution_id: UUID,
        tenant_id: str,
        idempotency_key: str,
        requested_by: str,
        facts: Mapping[str, Any],
        facts_hash: str,
    ) -> ReportJobRecord:
        for _attempt in range(5):
            existing = await self.session.scalar(
                select(ReportJobRecord).where(
                    ReportJobRecord.tenant_id == tenant_id,
                    ReportJobRecord.execution_id == execution_id,
                    ReportJobRecord.idempotency_key == idempotency_key,
                )
            )
            if existing is not None:
                return existing
            latest = await self.session.scalar(
                select(func.max(ReportJobRecord.version)).where(
                    ReportJobRecord.execution_id == execution_id
                )
            )
            record = ReportJobRecord(
                execution_id=execution_id,
                tenant_id=tenant_id,
                version=(latest or 0) + 1,
                idempotency_key=idempotency_key,
                facts=dict(facts),
                facts_hash=facts_hash,
                requested_by=requested_by,
            )
            try:
                async with self.session.begin_nested():
                    self.session.add(record)
                    await self.session.flush()
            except IntegrityError:
                continue
            return record
        existing = await self.session.scalar(
            select(ReportJobRecord).where(
                ReportJobRecord.tenant_id == tenant_id,
                ReportJobRecord.execution_id == execution_id,
                ReportJobRecord.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return cast(ReportJobRecord, existing)
        raise ReportingConflict("could not allocate report version")

    async def create_restore_request(
        self,
        *,
        task_id: UUID,
        tenant_id: str,
        source_version_id: UUID,
        semantic_source_version_id: UUID,
        target_version_id: UUID,
        preview_hash: str,
        deterministic_plan: Mapping[str, Any],
        covered_execution_ids: Sequence[UUID],
        requested_by: str,
        ai_candidate: Mapping[str, Any] | None = None,
        ai_provenance: Mapping[str, Any] | None = None,
    ) -> RestoreRequestRecord:
        existing = await self.get_restore_by_preview_hash(preview_hash)
        if existing is not None:
            if existing.tenant_id != tenant_id or existing.task_id != task_id:
                raise ReportingConflict("restore preview hash belongs to another scope")
            return existing
        record = RestoreRequestRecord(
            task_id=task_id,
            tenant_id=tenant_id,
            source_version_id=source_version_id,
            semantic_source_version_id=semantic_source_version_id,
            target_version_id=target_version_id,
            preview_hash=preview_hash,
            deterministic_plan=dict(deterministic_plan),
            covered_execution_ids=[str(item) for item in covered_execution_ids],
            requested_by=requested_by,
            ai_candidate=dict(ai_candidate) if ai_candidate is not None else None,
            ai_provenance=dict(ai_provenance) if ai_provenance is not None else None,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(record)
                await self.session.flush()
        except IntegrityError:
            existing = await self.get_restore_by_preview_hash(preview_hash)
            if (
                existing is not None
                and existing.tenant_id == tenant_id
                and existing.task_id == task_id
            ):
                return existing
            raise
        return record

    async def resolve_semantic_version(self, version_id: UUID, *, tenant_id: str) -> UUID:
        current = version_id
        seen: set[UUID] = set()
        while current not in seen:
            seen.add(current)
            semantic = await self.session.scalar(
                select(RestoreRequestRecord.target_version_id)
                .join(
                    RestoreExecutionLinkRecord,
                    RestoreExecutionLinkRecord.restore_request_id == RestoreRequestRecord.id,
                )
                .join(
                    RestoreExecutionResultRecord,
                    RestoreExecutionResultRecord.restore_execution_link_id
                    == RestoreExecutionLinkRecord.id,
                )
                .where(
                    RestoreExecutionResultRecord.output_version_id == current,
                    RestoreRequestRecord.tenant_id == tenant_id,
                )
            )
            if semantic is None:
                return current
            current = semantic
        raise ReportingConflict("restore semantic version cycle detected")

    async def get_report_for_job(self, job_id: UUID) -> GovernanceReportRecord | None:
        return cast(
            GovernanceReportRecord | None,
            await self.session.scalar(
                select(GovernanceReportRecord).where(GovernanceReportRecord.job_id == job_id)
            ),
        )

    async def get_job(self, job_id: UUID) -> ReportJobRecord | None:
        return await self.session.get(ReportJobRecord, job_id)

    async def finish_report(
        self,
        job: ReportJobRecord,
        *,
        facts: Mapping[str, Any],
        facts_hash: str,
        content: Mapping[str, Any],
        html_content: str,
        html_hash: str,
        provenance: Mapping[str, Any],
        generated_by: str,
    ) -> GovernanceReportRecord:
        existing = await self.get_report_for_job(job.id)
        if existing is not None:
            return existing
        report = GovernanceReportRecord(
            job_id=job.id,
            execution_id=job.execution_id,
            version=job.version,
            facts=dict(facts),
            facts_hash=facts_hash,
            content=dict(content),
            html_content=html_content,
            html_hash=html_hash,
            provenance=dict(provenance),
            generated_by=generated_by,
        )
        job.status = "succeeded"
        try:
            async with self.session.begin_nested():
                self.session.add(report)
                await self.session.flush()
        except IntegrityError:
            existing = await self.get_report_for_job(job.id)
            if existing is not None:
                return existing
            raise
        return report

    async def get_report(self, report_id: UUID) -> GovernanceReportRecord | None:
        return await self.session.get(GovernanceReportRecord, report_id)

    async def list_reports(self, execution_id: UUID) -> tuple[GovernanceReportRecord, ...]:
        records = await self.session.scalars(
            select(GovernanceReportRecord)
            .where(GovernanceReportRecord.execution_id == execution_id)
            .order_by(GovernanceReportRecord.version)
        )
        return tuple(records)

    async def link_restore_execution(
        self,
        *,
        restore_request_id: UUID,
        compensation_plan_id: UUID,
        compensation_batch_id: UUID,
        output_version_id: UUID | None = None,
    ) -> RestoreExecutionLinkRecord:
        existing = await self.get_restore_link(restore_request_id)
        if existing is not None:
            if (
                existing.compensation_plan_id == compensation_plan_id
                and existing.compensation_batch_id == compensation_batch_id
            ):
                return existing
            raise ReportingConflict("restore request is already linked to another compensation")
        record = RestoreExecutionLinkRecord(
            restore_request_id=restore_request_id,
            compensation_plan_id=compensation_plan_id,
            compensation_batch_id=compensation_batch_id,
            output_version_id=output_version_id,
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def get_restore_request(self, request_id: UUID) -> RestoreRequestRecord | None:
        return await self.session.get(RestoreRequestRecord, request_id)

    async def get_restore_request_for_update(
        self, request_id: UUID
    ) -> RestoreRequestRecord | None:
        return cast(
            RestoreRequestRecord | None,
            await self.session.scalar(
                select(RestoreRequestRecord)
                .where(RestoreRequestRecord.id == request_id)
                .with_for_update()
            ),
        )

    async def get_restore_by_preview_hash(self, preview_hash: str) -> RestoreRequestRecord | None:
        return cast(
            RestoreRequestRecord | None,
            await self.session.scalar(
                select(RestoreRequestRecord).where(
                    RestoreRequestRecord.preview_hash == preview_hash
                )
            ),
        )

    async def get_restore_link(self, restore_request_id: UUID) -> RestoreExecutionLinkRecord | None:
        return cast(
            RestoreExecutionLinkRecord | None,
            await self.session.scalar(
                select(RestoreExecutionLinkRecord).where(
                    RestoreExecutionLinkRecord.restore_request_id == restore_request_id
                )
            ),
        )

    async def get_restore_link_for_batch(self, batch_id: UUID) -> RestoreExecutionLinkRecord | None:
        return cast(
            RestoreExecutionLinkRecord | None,
            await self.session.scalar(
                select(RestoreExecutionLinkRecord).where(
                    RestoreExecutionLinkRecord.compensation_batch_id == batch_id
                )
            ),
        )

    async def restore_state_for_execution(
        self, execution_id: UUID, *, tenant_id: str
    ) -> RestoreState:
        if await self.get_restore_link_for_batch(execution_id) is not None:
            return RestoreState.RESTORE_EXECUTION
        requests = tuple(
            await self.session.scalars(
                select(RestoreRequestRecord)
                .where(RestoreRequestRecord.tenant_id == tenant_id)
                .order_by(RestoreRequestRecord.created_at.desc(), RestoreRequestRecord.id.desc())
            )
        )
        for request in requests:
            if str(execution_id) not in request.covered_execution_ids:
                continue
            link = await self.get_restore_link(request.id)
            if link is None:
                return RestoreState.PREVIEWED
            result = await self.session.scalar(
                select(RestoreExecutionResultRecord).where(
                    RestoreExecutionResultRecord.restore_execution_link_id == link.id
                )
            )
            return RestoreState.RESTORED if result is not None else RestoreState.CONFIRMED
        return RestoreState.NOT_RESTORED

    async def append_restore_result(
        self,
        *,
        restore_execution_link_id: UUID,
        output_version_id: UUID,
        verified_content_hash: str,
    ) -> RestoreExecutionResultRecord:
        existing = await self.session.scalar(
            select(RestoreExecutionResultRecord).where(
                RestoreExecutionResultRecord.restore_execution_link_id == restore_execution_link_id
            )
        )
        if existing is not None:
            return existing
        record = RestoreExecutionResultRecord(
            restore_execution_link_id=restore_execution_link_id,
            output_version_id=output_version_id,
            verified_content_hash=verified_content_hash,
        )
        self.session.add(record)
        await self.session.flush()
        return record
