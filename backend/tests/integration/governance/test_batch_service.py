from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.ai.analysis_service import AnalysisService
from app.ai.job_service import AnalysisJobService
from app.core.security import OperatorContext
from app.governance.batch_service import BatchConflict, BatchGovernanceService
from app.governance.proposal_service import ProposalService
from app.repositories.analysis_jobs import AnalysisJobRepository
from app.repositories.proposals import ProposalRepository
from app.schemas.analysis_jobs import AnalysisWorkItemStatus
from app.schemas.batch_governance import (
    BatchPreviewRequest,
    ConfirmBatchProposalRequest,
)
from app.schemas.differences import DifferenceType
from app.schemas.governance import RecommendedAction
from app.schemas.proposals import CreateManualProposalRequest, ProposalSource, ProposalStatus
from tests.integration.ai.test_analysis_service import seed_difference
from tests.integration.ai.test_analysis_v3_service import V3AgentSpy

OPERATOR = OperatorContext(operator_id="operator-1", tenant_id="school-1")


async def analyzed_terminal_job(session):
    difference = await seed_difference(session, DifferenceType.ATTRIBUTE_CONFLICT)
    analysis = await AnalysisService(
        session,
        agent=V3AgentSpy(),
        operator=OPERATOR,
    ).analyze_v3(difference.id)
    job = await AnalysisJobService(session, operator=OPERATOR).create_job(
        difference.task_id,
        idempotency_key=f"batch-job-{uuid4()}",
    )
    item = await AnalysisJobRepository(session).claim_next(
        job.id,
        worker_id="test-worker",
        lease_seconds=60,
    )
    assert item is not None
    await AnalysisJobRepository(session).complete_item(
        item.id,
        worker_id="test-worker",
        outcome=AnalysisWorkItemStatus.SUCCEEDED,
        result_id=analysis.id,
    )
    return difference, analysis, job


@pytest.mark.asyncio
async def test_summary_and_preview_include_safe_recommended_v3_path(session) -> None:
    difference, _analysis, job = await analyzed_terminal_job(session)
    service = BatchGovernanceService(session, operator=OPERATOR, signing_secret=b"test-secret")

    summary = await service.summary(difference.task_id)
    preview = await service.preview(
        difference.task_id,
        BatchPreviewRequest(analysis_job_id=job.id),
    )

    assert summary.terminal is True
    assert summary.entity_types[0].issue_count == 1
    assert summary.entity_types[0].proposal_ready == 1
    assert len(preview.included) == 1
    assert preview.included[0].title == "更新教师手机号"
    assert preview.excluded == ()


@pytest.mark.asyncio
async def test_preview_rejects_non_terminal_analysis_job(session) -> None:
    difference = await seed_difference(session, DifferenceType.ATTRIBUTE_CONFLICT)
    job = await AnalysisJobService(session, operator=OPERATOR).create_job(
        difference.task_id,
        idempotency_key="batch-running-job",
    )

    with pytest.raises(BatchConflict, match="not terminal"):
        await BatchGovernanceService(
            session,
            operator=OPERATOR,
            signing_secret=b"test-secret",
        ).preview(
            difference.task_id,
            BatchPreviewRequest(analysis_job_id=job.id),
        )


@pytest.mark.asyncio
async def test_canceled_incomplete_job_does_not_expose_terminal_summary(session) -> None:
    difference = await seed_difference(session, DifferenceType.ATTRIBUTE_CONFLICT)
    job = await AnalysisJobService(session, operator=OPERATOR).create_job(
        difference.task_id,
        idempotency_key="batch-canceled-job",
    )
    await AnalysisJobRepository(session).cancel(job.id)

    summary = await BatchGovernanceService(
        session,
        operator=OPERATOR,
        signing_secret=b"test-secret",
    ).summary(difference.task_id)

    assert summary.terminal is False


@pytest.mark.asyncio
async def test_batch_confirmation_is_idempotent_and_only_creates_pending_proposal(session) -> None:
    difference, _analysis, job = await analyzed_terminal_job(session)
    service = BatchGovernanceService(session, operator=OPERATOR, signing_secret=b"test-secret")
    preview = await service.preview(
        difference.task_id,
        BatchPreviewRequest(analysis_job_id=job.id),
    )
    request = ConfirmBatchProposalRequest(
        preview_token=preview.preview_token,
        idempotency_key="batch-confirm-1",
    )

    first = await service.confirm(difference.task_id, request)
    second = await service.confirm(difference.task_id, request)
    proposals = await ProposalRepository(session).list_for_difference(difference.id)

    assert first == second
    assert first.created == 1
    assert first.failed == 0
    assert len(proposals) == 1
    assert proposals[0].status is ProposalStatus.PENDING_EXECUTION


@pytest.mark.asyncio
async def test_batch_confirmation_rejects_reused_key_for_different_preview(session) -> None:
    difference, analysis, first_job = await analyzed_terminal_job(session)
    service = BatchGovernanceService(session, operator=OPERATOR, signing_secret=b"test-secret")
    first_preview = await service.preview(
        difference.task_id,
        BatchPreviewRequest(analysis_job_id=first_job.id),
    )
    await service.confirm(
        difference.task_id,
        ConfirmBatchProposalRequest(
            preview_token=first_preview.preview_token,
            idempotency_key="batch-confirm-reused-key",
        ),
    )
    second_job = await AnalysisJobService(session, operator=OPERATOR).create_job(
        difference.task_id,
        idempotency_key="batch-second-analysis-job",
    )
    second_item = await AnalysisJobRepository(session).claim_next(
        second_job.id,
        worker_id="test-worker-2",
        lease_seconds=60,
    )
    assert second_item is not None
    await AnalysisJobRepository(session).complete_item(
        second_item.id,
        worker_id="test-worker-2",
        outcome=AnalysisWorkItemStatus.SUCCEEDED,
        result_id=analysis.id,
    )
    second_preview = await service.preview(
        difference.task_id,
        BatchPreviewRequest(analysis_job_id=second_job.id),
    )
    assert second_preview.preview_token != first_preview.preview_token

    with pytest.raises(BatchConflict, match="idempotency"):
        await service.confirm(
            difference.task_id,
            ConfirmBatchProposalRequest(
                preview_token=second_preview.preview_token,
                idempotency_key="batch-confirm-reused-key",
            ),
        )


@pytest.mark.asyncio
async def test_batch_confirmation_locks_task_before_processing(session) -> None:
    difference, _analysis, job = await analyzed_terminal_job(session)
    service = BatchGovernanceService(session, operator=OPERATOR, signing_secret=b"test-secret")
    preview = await service.preview(
        difference.task_id,
        BatchPreviewRequest(analysis_job_id=job.id),
    )
    original = service.tasks.get_for_update
    service.tasks.get_for_update = AsyncMock(wraps=original)

    await service.confirm(
        difference.task_id,
        ConfirmBatchProposalRequest(
            preview_token=preview.preview_token,
            idempotency_key="batch-confirm-lock",
        ),
    )

    service.tasks.get_for_update.assert_awaited_once_with(difference.task_id)


@pytest.mark.asyncio
async def test_batch_confirmation_does_not_supersede_manual_proposal_created_after_preview(
    session,
) -> None:
    difference, _analysis, job = await analyzed_terminal_job(session)
    batch = BatchGovernanceService(session, operator=OPERATOR, signing_secret=b"test-secret")
    preview = await batch.preview(
        difference.task_id,
        BatchPreviewRequest(analysis_job_id=job.id),
    )
    await ProposalService(session, operator=OPERATOR).confirm_manual(
        difference.id,
        CreateManualProposalRequest(
            expected_difference_version=difference.version,
            operation_type=RecommendedAction.UPDATE,
            target_entity_id=difference.evidence.target_entity_id,
            changes={"phone": "13700000000"},
            rationale="管理员已核实并选择人工修改手机号。",
        ),
    )

    result = await batch.confirm(
        difference.task_id,
        ConfirmBatchProposalRequest(
            preview_token=preview.preview_token,
            idempotency_key="batch-confirm-after-manual",
        ),
    )
    proposals = await ProposalRepository(session).list_for_difference(difference.id)

    assert result.created == 0
    assert result.skipped == 1
    assert result.items[0].reason == "已有待执行方案"
    assert len(proposals) == 1
    assert proposals[0].proposal_source is ProposalSource.OPERATOR
