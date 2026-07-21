import hashlib
import json
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import OperatorContext
from app.executions.record_service import ExecutionRecordService
from app.models.analyses import AnalysisRecord
from app.models.differences import DifferenceRecord
from app.models.executions import ExecutionOperationRecord
from app.repositories.reporting import ReportingRepository
from app.schemas.executions import ExecutionBatchStatus
from app.schemas.reporting import ExecutionFactBundle


class ExecutionFactCollector:
    def __init__(self, session: AsyncSession, *, operator: OperatorContext) -> None:
        self.session = session
        self.operator = operator
        self.records = ExecutionRecordService(session, operator=operator)
        self.reporting = ReportingRepository(session)

    async def collect(self, execution_id: UUID) -> ExecutionFactBundle:
        detail = await self.records.get_detail(execution_id)
        if detail.status not in {
            ExecutionBatchStatus.SUCCEEDED,
            ExecutionBatchStatus.PARTIAL_FAILURE,
        }:
            raise ValueError("execution is not reportable")
        operation_records = tuple(
            await self.session.scalars(
                select(ExecutionOperationRecord)
                .where(ExecutionOperationRecord.batch_id == execution_id)
                .order_by(ExecutionOperationRecord.created_at, ExecutionOperationRecord.id)
            )
        )
        analysis_by_id = {
            item.id: item
            for item in await self.session.scalars(
                select(AnalysisRecord).where(
                    AnalysisRecord.id.in_({item.analysis_id for item in operation_records})
                )
            )
        }
        differences = tuple(
            await self.session.scalars(
                select(DifferenceRecord).where(
                    DifferenceRecord.id.in_({item.difference_id for item in operation_records})
                )
            )
        )
        statistics: dict[str, int] = {}
        for difference in differences:
            current_count = statistics.get(difference.difference_type, 0)
            statistics[difference.difference_type] = current_count + 1
        operations = tuple(operation.model_dump(mode="json") for operation in detail.operations)
        failures = tuple(
            operation
            for operation in operations
            if operation.get("attempts")
            and operation["attempts"][-1].get("status") != "succeeded"
        )
        return ExecutionFactBundle(
            execution_id=detail.id,
            task_id=detail.task_id,
            plan_id=detail.plan_id,
            plan_version=detail.plan_version,
            source_snapshot_id=detail.source_snapshot_id,
            target_snapshot_id=detail.target_snapshot_id,
            input_target_version_id=detail.input_target_version_id,
            output_target_version_ids=detail.output_target_version_ids,
            status=detail.status.value,
            confirmed_by=detail.confirmed_by,
            confirmed_at=detail.confirmed_at,
            operations=operations,
            analyses=tuple(
                {
                    "id": str(analysis.id),
                    "analysis_version": analysis.analysis_version,
                    "status": analysis.status,
                    "output": analysis.output,
                    "provider": analysis.provider,
                    "model": analysis.model,
                    "skill_name": analysis.skill_name,
                    "skill_version": analysis.skill_version,
                    "prompt_version": analysis.prompt_version,
                }
                for item in operation_records
                if (analysis := analysis_by_id.get(item.analysis_id)) is not None
            ),
            difference_statistics=statistics,
            failures=failures,
            audit_events=tuple(event.model_dump(mode="json") for event in detail.audit_events),
            restore_state=await self.reporting.restore_state_for_execution(
                execution_id, tenant_id=self.operator.tenant_id
            ),
        )


def facts_hash(facts: ExecutionFactBundle) -> str:
    payload = json.dumps(
        facts.model_dump(mode="json"),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()
