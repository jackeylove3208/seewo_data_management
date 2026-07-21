import asyncio
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analyses import AnalysisRecord
from app.models.differences import DifferenceRecord
from app.models.executions import TargetVersionRecord
from app.models.proposals import GovernanceProposalRecord
from app.repositories.executions import ExecutionRepository
from app.schemas.executions import (
    GovernanceOperation,
    GovernancePlan,
    PreflightConflict,
    PreflightConflictCode,
    PreflightResult,
    json_values_equal,
)


class ExecutionPreflight:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.executions = ExecutionRepository(session)

    async def check(self, plan: GovernancePlan) -> PreflightResult:
        current_target = await self.current_target_version(plan.task_id)
        if current_target is None:
            raise LookupError("target version not found")
        conflicts: list[PreflightConflict] = []
        current_hash = await _current_file_hash(current_target)
        expected_target = f"sha256:{current_hash}"
        if plan.target_version != expected_target:
            conflicts.append(
                PreflightConflict(
                    code=PreflightConflictCode.TARGET_VERSION_DRIFT,
                    message="target version changed after preview",
                )
            )
        for operation in plan.operations:
            conflicts.extend(await self._operation_conflicts(operation, plan))
        return PreflightResult(
            plan_id=plan.id,
            plan_version=plan.version,
            target_version_id=current_target.id,
            target_version=expected_target,
            conflicts=tuple(conflicts),
            valid=not conflicts,
        )

    async def current_target_version(self, task_id: UUID) -> TargetVersionRecord | None:
        return await self.executions.current_target_version(task_id)

    async def _operation_conflicts(
        self,
        operation: GovernanceOperation,
        plan: GovernancePlan,
    ) -> Sequence[PreflightConflict]:
        conflicts: list[PreflightConflict] = []
        proposal = await self.session.get(GovernanceProposalRecord, operation.proposal.proposal_id)
        latest_proposal_version = await self.session.scalar(
            select(func.max(GovernanceProposalRecord.proposal_version)).where(
                GovernanceProposalRecord.difference_id == operation.difference_id,
                GovernanceProposalRecord.difference_version == operation.difference_version,
            )
        )
        if (
            proposal is None
            or proposal.status != "pending_execution"
            or proposal.proposal_version != operation.proposal.proposal_version
            or latest_proposal_version != operation.proposal.proposal_version
        ):
            conflicts.append(
                PreflightConflict(
                    operation_id=operation.id,
                    code=PreflightConflictCode.PROPOSAL_VERSION_DRIFT,
                    message="proposal is no longer the current pending version",
                )
            )
        difference = await self.session.get(DifferenceRecord, operation.difference_id)
        if difference is None or difference.version != operation.difference_version:
            conflicts.append(
                PreflightConflict(
                    operation_id=operation.id,
                    code=PreflightConflictCode.DIFFERENCE_VERSION_DRIFT,
                    message="difference version changed after preview",
                )
            )
        analysis = await self.session.get(AnalysisRecord, operation.analysis_id)
        if (
            analysis is None
            or analysis.analysis_version != operation.analysis_version
            or analysis.difference_version != operation.difference_version
            or analysis.status not in {"succeeded", "manual_review"}
        ):
            conflicts.append(
                PreflightConflict(
                    operation_id=operation.id,
                    code=PreflightConflictCode.ANALYSIS_VERSION_DRIFT,
                    message="analysis is no longer valid for this difference",
                )
            )
        selected_ids = {item.id for item in plan.operations}
        if not operation.dependencies <= selected_ids:
            conflicts.append(
                PreflightConflict(
                    operation_id=operation.id,
                    code=PreflightConflictCode.DEPENDENCY_MISSING,
                    message="operation dependency is missing from the plan",
                )
            )
        if operation.before is not None and difference is not None:
            actual = _difference_target_facts(difference.evidence)
            if any(
                field not in actual or not json_values_equal(expected, actual[field])
                for field, expected in operation.before.items()
            ):
                conflicts.append(
                    PreflightConflict(
                        operation_id=operation.id,
                        code=PreflightConflictCode.BEFORE_VALUE_DRIFT,
                        message="target value changed after proposal review",
                    )
                )
        return conflicts


def _difference_target_facts(evidence: dict[str, object]) -> dict[str, object]:
    payload = evidence.get("target_payload")
    facts = dict(payload) if isinstance(payload, dict) else {}
    fields = evidence.get("fields")
    if isinstance(fields, list):
        for item in fields:
            if isinstance(item, dict) and isinstance(item.get("field"), str):
                facts[item["field"]] = item.get("target_value")
    return facts


async def _current_file_hash(version: TargetVersionRecord) -> str:
    path = Path(version.storage_path)
    if not await asyncio.to_thread(path.is_file):
        return version.file_sha256
    return await asyncio.to_thread(_sha256_file, path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def plan_from_record_payload(
    *,
    plan_id: UUID,
    version: int,
    task_id: UUID,
    source_snapshot_id: UUID,
    target_snapshot_id: UUID,
    target_version: str,
    proposal_versions: list[dict[str, object]],
    operations: list[dict[str, object]],
    content_hash: str,
) -> GovernancePlan:
    return GovernancePlan.model_validate_json(
        json.dumps(
            {
                "id": str(plan_id),
                "version": version,
                "task_id": str(task_id),
                "source_snapshot_id": str(source_snapshot_id),
                "target_snapshot_id": str(target_snapshot_id),
                "target_version": target_version,
                "proposals": proposal_versions,
                "operations": operations,
                "content_hash": content_hash,
            }
        )
    )
