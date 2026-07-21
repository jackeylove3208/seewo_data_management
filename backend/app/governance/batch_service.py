import base64
import hashlib
import hmac
import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import OperatorContext
from app.governance.proposal_service import ProposalConflict, ProposalService
from app.models.proposal_batches import ProposalBatchRecord
from app.repositories.analyses import ANALYSIS_V3_VERSION, AnalysisRepository
from app.repositories.analysis_jobs import AnalysisJobRepository
from app.repositories.differences import DifferenceRepository
from app.repositories.proposals import ProposalRepository
from app.repositories.tasks import TaskRepository
from app.schemas.analysis_jobs import AnalysisJobStatus
from app.schemas.batch_governance import (
    BatchExcludedItem,
    BatchExclusionReason,
    BatchItemResult,
    BatchPreviewItem,
    BatchPreviewRequest,
    BatchProposalPreview,
    BatchProposalResult,
    ConfirmBatchProposalRequest,
    EntityIssueSummary,
    TaskAnalysisSummary,
)
from app.schemas.canonical_entities import EntityType
from app.schemas.governance import (
    AnalysisResult,
    AnalysisStatus,
    AutoExecutableResolution,
    CauseAnalysisV3,
    ManualResolution,
    NeedsInformationResolution,
    RiskLevel,
)
from app.schemas.proposals import CreateAIProposalRequest

TERMINAL_JOB_STATUSES = {
    AnalysisJobStatus.COMPLETED.value,
    AnalysisJobStatus.COMPLETED_WITH_FAILURES.value,
}

EXCLUSION_LABELS = {
    BatchExclusionReason.HIGH_RISK: "高风险，需人工确认",
    BatchExclusionReason.NEEDS_INFORMATION: "需要补充信息",
    BatchExclusionReason.MANUAL_ONLY: "仅支持人工处理",
    BatchExclusionReason.ANALYSIS_FAILED: "分析失败",
    BatchExclusionReason.STALE: "数据版本已变化",
    BatchExclusionReason.EXISTING_PROPOSAL: "已有待执行方案",
    BatchExclusionReason.NO_RECOMMENDED_ACTION: "没有可采用的推荐修改",
}


class BatchConflict(ValueError):
    pass


class BatchGovernanceService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        operator: OperatorContext,
        signing_secret: bytes,
    ) -> None:
        if len(signing_secret) < 8:
            raise ValueError("batch preview signing secret is too short")
        self.session = session
        self.operator = operator
        self.signing_secret = signing_secret
        self.tasks = TaskRepository(session)
        self.jobs = AnalysisJobRepository(session)
        self.differences = DifferenceRepository(session)
        self.analyses = AnalysisRepository(session)
        self.proposals = ProposalRepository(session)

    async def summary(self, task_id: UUID) -> TaskAnalysisSummary:
        await self._require_task(task_id)
        job = await self.jobs.current_for_task(task_id, self.operator.tenant_id)
        terminal = bool(
            job is not None and job.status in TERMINAL_JOB_STATUSES and job.completed == job.total
        )
        counts: dict[EntityType, dict[str, int]] = defaultdict(
            lambda: {
                "issue_count": 0,
                "proposal_ready": 0,
                "needs_information": 0,
                "manual_only": 0,
                "failed": 0,
            }
        )
        for difference in await self.differences.for_task(task_id):
            values = counts[difference.entity_type]
            values["issue_count"] += 1
            analysis = await self.analyses.get_for_difference(
                difference.id,
                difference.version,
                ANALYSIS_V3_VERSION,
            )
            reason, solution = await self._classify(difference.id, difference.version, analysis)
            if reason is None and solution is not None:
                values["proposal_ready"] += 1
            elif reason is BatchExclusionReason.NEEDS_INFORMATION:
                values["needs_information"] += 1
            elif reason in {BatchExclusionReason.MANUAL_ONLY, BatchExclusionReason.HIGH_RISK}:
                values["manual_only"] += 1
            elif reason is BatchExclusionReason.ANALYSIS_FAILED:
                values["failed"] += 1
        return TaskAnalysisSummary(
            task_id=task_id,
            analysis_job_id=job.id if job is not None else None,
            job_status=AnalysisJobStatus(job.status) if job is not None else None,
            terminal=terminal,
            entity_types=tuple(
                EntityIssueSummary(entity_type=entity_type, **values)
                for entity_type, values in sorted(counts.items(), key=lambda item: item[0].value)
            ),
        )

    async def preview(
        self,
        task_id: UUID,
        request: BatchPreviewRequest,
    ) -> BatchProposalPreview:
        await self._require_task(task_id)
        job = await self.jobs.get_for_tenant(request.analysis_job_id, self.operator.tenant_id)
        if job is None or job.task_id != task_id:
            raise LookupError("analysis job not found")
        current_job = await self.jobs.current_for_task(task_id, self.operator.tenant_id)
        if current_job is None or current_job.id != job.id:
            raise BatchConflict("analysis job is no longer current")
        if job.status not in TERMINAL_JOB_STATUSES or job.completed != job.total:
            raise BatchConflict("analysis job is not terminal")
        included: list[BatchPreviewItem] = []
        excluded: list[BatchExcludedItem] = []
        for difference in await self.differences.for_task(task_id):
            if (
                request.entity_type is not None
                and difference.entity_type is not request.entity_type
            ):
                continue
            analysis = await self.analyses.get_for_difference(
                difference.id,
                difference.version,
                ANALYSIS_V3_VERSION,
            )
            reason, solution = await self._classify(difference.id, difference.version, analysis)
            if reason is not None or solution is None or analysis is None:
                excluded_reason = reason or BatchExclusionReason.NO_RECOMMENDED_ACTION
                excluded.append(
                    BatchExcludedItem(
                        difference_id=difference.id,
                        entity_type=difference.entity_type,
                        reason=excluded_reason,
                        reason_label=EXCLUSION_LABELS[excluded_reason],
                    )
                )
                continue
            included.append(
                BatchPreviewItem(
                    difference_id=difference.id,
                    difference_version=difference.version,
                    analysis_id=analysis.id,
                    solution_id=solution.solution_id,
                    entity_type=difference.entity_type,
                    title=solution.title,
                    operation_type=solution.action.operation_type,
                    changes=solution.action.proposed_changes,
                    risk=solution.risk,
                )
            )
        token = self._encode_token(
            {
                "task_id": str(task_id),
                "tenant_id": self.operator.tenant_id,
                "analysis_job_id": str(job.id),
                "expires_at": (datetime.now(UTC) + timedelta(minutes=15)).isoformat(),
                "items": [
                    {
                        "difference_id": str(item.difference_id),
                        "difference_version": item.difference_version,
                        "analysis_id": str(item.analysis_id),
                        "solution_id": item.solution_id,
                    }
                    for item in included
                ],
            }
        )
        return BatchProposalPreview(
            task_id=task_id,
            analysis_job_id=job.id,
            preview_token=token,
            included=tuple(included),
            excluded=tuple(excluded),
        )

    async def confirm(
        self,
        task_id: UUID,
        request: ConfirmBatchProposalRequest,
    ) -> BatchProposalResult:
        await self._require_task_for_update(task_id)
        preview_hash = hashlib.sha256(request.preview_token.encode()).hexdigest()
        existing = await self.session.scalar(
            select(ProposalBatchRecord).where(
                ProposalBatchRecord.task_id == task_id,
                ProposalBatchRecord.tenant_id == self.operator.tenant_id,
                ProposalBatchRecord.idempotency_key == request.idempotency_key,
            )
        )
        if existing is not None:
            if existing.preview_hash != preview_hash:
                raise BatchConflict("idempotency key was already used for a different preview")
            return BatchProposalResult.model_validate(existing.result)
        payload = self._decode_token(request.preview_token)
        if (
            payload.get("task_id") != str(task_id)
            or payload.get("tenant_id") != self.operator.tenant_id
        ):
            raise BatchConflict("batch preview token scope does not match task")
        try:
            expires_at = datetime.fromisoformat(str(payload["expires_at"]))
        except (KeyError, ValueError) as error:
            raise BatchConflict("batch preview token is invalid") from error
        if expires_at < datetime.now(UTC):
            raise BatchConflict("batch preview token has expired")
        results: list[BatchItemResult] = []
        proposal_service = ProposalService(self.session, operator=self.operator)
        token_items = payload.get("items")
        if not isinstance(token_items, list) or not all(
            isinstance(item, dict) for item in token_items
        ):
            raise BatchConflict("batch preview token is invalid")
        for item in token_items:
            difference_id = UUID(item["difference_id"])
            try:
                difference = await self.differences.get_for_update(difference_id)
                if difference is None or difference.tenant_id != self.operator.tenant_id:
                    raise ProposalConflict("差异记录不存在")
                expected_version = int(item["difference_version"])
                if difference.version != expected_version:
                    raise ProposalConflict("数据版本已变化")
                if await self.proposals.get_current(difference_id, expected_version) is not None:
                    raise ProposalConflict("已有待执行方案")
                async with self.session.begin_nested():
                    proposal = await proposal_service.confirm_ai(
                        difference_id,
                        CreateAIProposalRequest(
                            analysis_id=UUID(item["analysis_id"]),
                            option_id=item["solution_id"],
                            expected_difference_version=expected_version,
                        ),
                    )
            except ProposalConflict as error:
                results.append(
                    BatchItemResult(
                        difference_id=difference_id,
                        status="skipped",
                        reason=_localized_conflict_reason(error),
                    )
                )
            except Exception:
                results.append(
                    BatchItemResult(
                        difference_id=difference_id,
                        status="failed",
                        reason="处理失败，请重试",
                    )
                )
            else:
                results.append(
                    BatchItemResult(
                        difference_id=difference_id,
                        status="created",
                        proposal_id=proposal.id,
                    )
                )
        result = BatchProposalResult(
            task_id=task_id,
            created=sum(item.status == "created" for item in results),
            skipped=sum(item.status == "skipped" for item in results),
            failed=sum(item.status == "failed" for item in results),
            items=tuple(results),
        )
        self.session.add(
            ProposalBatchRecord(
                task_id=task_id,
                tenant_id=self.operator.tenant_id,
                idempotency_key=request.idempotency_key,
                preview_hash=preview_hash,
                created_by=self.operator.operator_id,
                result=result.model_dump(mode="json"),
            )
        )
        await self.session.flush()
        return result

    async def _classify(
        self,
        difference_id: UUID,
        difference_version: int,
        analysis: AnalysisResult | None,
    ) -> tuple[BatchExclusionReason | None, AutoExecutableResolution | None]:
        if analysis is None or not isinstance(analysis.output, CauseAnalysisV3):
            return BatchExclusionReason.ANALYSIS_FAILED, None
        if analysis.difference_version != difference_version:
            return BatchExclusionReason.STALE, None
        if await self.proposals.get_current(difference_id, difference_version) is not None:
            return BatchExclusionReason.EXISTING_PROPOSAL, None
        recommended = next(
            (
                solution
                for solution in analysis.output.solutions
                if solution.solution_id == analysis.output.recommended_solution_id
            ),
            None,
        )
        if isinstance(recommended, NeedsInformationResolution):
            return BatchExclusionReason.NEEDS_INFORMATION, None
        if isinstance(recommended, ManualResolution):
            return BatchExclusionReason.MANUAL_ONLY, None
        if not isinstance(recommended, AutoExecutableResolution):
            return BatchExclusionReason.NO_RECOMMENDED_ACTION, None
        if recommended.risk is RiskLevel.HIGH:
            return BatchExclusionReason.HIGH_RISK, None
        if analysis.status is not AnalysisStatus.SUCCEEDED:
            return BatchExclusionReason.ANALYSIS_FAILED, None
        return None, recommended

    async def _require_task(self, task_id: UUID) -> None:
        task = await self.tasks.get(task_id)
        if task is None or task.tenant_id != self.operator.tenant_id:
            raise LookupError("reconciliation task not found")

    async def _require_task_for_update(self, task_id: UUID) -> None:
        task = await self.tasks.get_for_update(task_id)
        if task is None or task.tenant_id != self.operator.tenant_id:
            raise LookupError("reconciliation task not found")

    def _encode_token(self, payload: dict[str, object]) -> str:
        encoded = (
            base64.urlsafe_b64encode(
                json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode()
            )
            .decode()
            .rstrip("=")
        )
        signature = hmac.new(self.signing_secret, encoded.encode(), hashlib.sha256).hexdigest()
        return f"{encoded}.{signature}"

    def _decode_token(self, token: str) -> dict[str, object]:
        try:
            encoded, signature = token.rsplit(".", 1)
            expected = hmac.new(
                self.signing_secret,
                encoded.encode(),
                hashlib.sha256,
            ).hexdigest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError
            padded = encoded + "=" * (-len(encoded) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded).decode())
            if not isinstance(payload, dict):
                raise ValueError
            return payload
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise BatchConflict("batch preview token is invalid") from error


def _localized_conflict_reason(error: ProposalConflict) -> str:
    message = str(error)
    if "stale" in message or "version" in message or "版本" in message:
        return "数据版本已变化"
    if "proposal" in message or "方案" in message:
        return "已有待执行方案"
    if "analysis" in message or "分析" in message:
        return "分析方案已失效"
    return "当前数据不再满足批量处理条件"
