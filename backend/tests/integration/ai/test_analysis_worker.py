import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, update

from app.ai import worker as worker_module
from app.ai.agent import AgentProviderFailure, AgentResult
from app.ai.analysis_service import AnalysisService
from app.ai.job_service import AnalysisJobService
from app.ai.providers.base import ModelUsage, TransientModelError
from app.ai.worker import AnalysisWorker, effective_worker_concurrency
from app.core.config import Settings
from app.core.database import Database
from app.core.security import OperatorContext
from app.models.analysis_jobs import AnalysisWorkItemRecord
from app.models.differences import DifferenceRecord
from app.repositories.analysis_jobs import AnalysisJobRepository
from app.schemas.analysis_jobs import AnalysisJobStatus, AnalysisWorkItemStatus
from app.schemas.differences import DifferenceType
from app.schemas.governance import (
    AnalysisProvenance,
    AutoExecutableResolution,
    CauseAnalysisV3,
    ProposedFieldChange,
    RecommendedAction,
    ResolutionAction,
    RiskLevel,
)
from tests.integration.ai.test_analysis_service import seed_difference

OPERATOR = OperatorContext(operator_id="operator-1", tenant_id="school-1")


def test_sqlite_worker_uses_single_concurrency_lane(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'worker.db'}",
        analysis_worker_concurrency=4,
    )
    database = Database(settings.database_url)

    assert effective_worker_concurrency(settings, database) == 1


def provenance() -> AnalysisProvenance:
    return AnalysisProvenance(
        provider="test-provider",
        model="test-model",
        skill_name="analyze-data-difference",
        skill_version="1.0.0",
        prompt_version="analysis-prompt-v3",
        usage=ModelUsage(input_tokens=3, output_tokens=2),
        generated_at=datetime.now(UTC),
    )


class SuccessfulAgent:
    def __init__(self, session) -> None:
        self.session = session
        self.calls = 0

    async def analyze(self, request):
        self.calls += 1
        assert not self.session.in_transaction(), "model call must not hold a DB transaction"
        evidence = request.input_payload["evidence"]
        field = evidence["fields"][0]
        return AgentResult(
            output=CauseAnalysisV3(
                locale="zh-CN",
                issue_title="教师手机号不一致",
                cause_summary="权威记录与希沃中的手机号不同。",
                evidence_summary="双方快照中的手机号字段已经完成比对。",
                business_impact="教师可能无法收到教学通知。",
                recommended_solution_id="solution-1",
                solutions=(
                    AutoExecutableResolution(
                        solution_id="solution-1",
                        title="更新教师手机号",
                        rationale="采用第三方权威记录中的手机号。",
                        risk=RiskLevel.LOW,
                        risk_reason="仅修改已确认教师的一项联系方式。",
                        confidence=0.96,
                        evidence_refs=(f"field:{field['field']}",),
                        recommended=True,
                        action=ResolutionAction(
                            operation_type=RecommendedAction.UPDATE,
                            target_entity_id=evidence["target_entity_id"],
                            proposed_changes=(
                                ProposedFieldChange(
                                    field=field["field"],
                                    before=field["target_value"],
                                    after=field["source_value"],
                                ),
                            ),
                        ),
                    ),
                ),
            ),
            provenance=provenance(),
        )


class TransientAgent:
    async def analyze(self, request):
        cause = TransientModelError("gateway timeout")
        raise AgentProviderFailure(
            "gateway timeout",
            provenance=provenance(),
            cause=cause,
        )


@pytest.mark.asyncio
async def test_job_service_creates_one_item_per_current_difference(session) -> None:
    difference = await seed_difference(session, DifferenceType.ATTRIBUTE_CONFLICT)

    job = await AnalysisJobService(session, operator=OPERATOR).create_job(
        difference.task_id,
        idempotency_key="job-create-1",
    )
    repeated = await AnalysisJobService(session, operator=OPERATOR).create_job(
        difference.task_id,
        idempotency_key="job-create-1",
    )

    assert job.id == repeated.id
    assert job.total == 1


@pytest.mark.asyncio
async def test_worker_commits_claim_before_model_call_and_completes_item(database, session) -> None:
    difference = await seed_difference(session, DifferenceType.ATTRIBUTE_CONFLICT)
    job = await AnalysisJobService(session, operator=OPERATOR).create_job(
        difference.task_id,
        idempotency_key="worker-success-1",
    )
    await session.commit()

    worker = AnalysisWorker(
        database.session_factory,
        analyzer_factory=lambda worker_session, operator: AnalysisService(
            worker_session,
            agent=SuccessfulAgent(worker_session),
            operator=operator,
        ),
        worker_id="worker-1",
        retry_wait_seconds=0,
    )

    assert await worker.run_once() is True
    async with database.session_factory() as check_session:
        refreshed = await AnalysisJobRepository(check_session).get(job.id)
        assert refreshed is not None
        assert refreshed.status == AnalysisJobStatus.COMPLETED.value
        assert refreshed.proposal_ready == 1
        item = await check_session.scalar(
            __import__("sqlalchemy")
            .select(AnalysisWorkItemRecord)
            .where(AnalysisWorkItemRecord.job_id == job.id)
        )
        assert item is not None
        assert item.status == AnalysisWorkItemStatus.SUCCEEDED.value
        assert item.result_id is not None


@pytest.mark.asyncio
async def test_worker_keeps_heartbeat_until_terminal_item_commit(
    database,
    session,
    monkeypatch,
) -> None:
    difference = await seed_difference(session, DifferenceType.ATTRIBUTE_CONFLICT)
    job = await AnalysisJobService(session, operator=OPERATOR).create_job(
        difference.task_id,
        idempotency_key="worker-heartbeat-finalize",
    )
    await session.commit()
    original_complete = AnalysisJobRepository.complete_item

    async def delayed_complete(self, *args, **kwargs):
        await asyncio.sleep(1.2)
        return await original_complete(self, *args, **kwargs)

    monkeypatch.setattr(AnalysisJobRepository, "complete_item", delayed_complete)
    worker = AnalysisWorker(
        database.session_factory,
        analyzer_factory=lambda worker_session, operator: AnalysisService(
            worker_session,
            agent=SuccessfulAgent(worker_session),
            operator=operator,
        ),
        worker_id="worker-heartbeat-finalize",
        lease_seconds=1,
    )

    assert await worker.run_once() is True
    async with database.session_factory() as check_session:
        refreshed = await AnalysisJobRepository(check_session).get(job.id)
        assert refreshed is not None
        assert refreshed.status == AnalysisJobStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_worker_syncs_workflow_after_releasing_item_completion_transaction(
    database,
    session,
    monkeypatch,
) -> None:
    difference = await seed_difference(session, DifferenceType.ATTRIBUTE_CONFLICT)
    await AnalysisJobService(session, operator=OPERATOR).create_job(
        difference.task_id,
        idempotency_key="worker-separate-workflow-sync",
    )
    await session.commit()
    completion_sessions: list[int] = []
    workflow_sessions: list[int] = []
    original_complete = AnalysisJobRepository.complete_item
    original_sync = AnalysisJobService.sync_workflow

    async def record_complete(self, *args, **kwargs):
        completion_sessions.append(id(self.session))
        return await original_complete(self, *args, **kwargs)

    async def record_sync(self, *args, **kwargs):
        workflow_sessions.append(id(self.session))
        return await original_sync(self, *args, **kwargs)

    monkeypatch.setattr(AnalysisJobRepository, "complete_item", record_complete)
    monkeypatch.setattr(AnalysisJobService, "sync_workflow", record_sync)
    worker = AnalysisWorker(
        database.session_factory,
        analyzer_factory=lambda worker_session, operator: AnalysisService(
            worker_session,
            agent=SuccessfulAgent(worker_session),
            operator=operator,
        ),
        worker_id="worker-separate-workflow-sync",
    )

    assert await worker.run_once() is True
    assert completion_sessions
    assert workflow_sessions
    assert completion_sessions[0] != workflow_sessions[0]


@pytest.mark.asyncio
async def test_worker_loop_continues_after_one_item_raises() -> None:
    stop = asyncio.Event()

    class FlakyWorker:
        calls = 0

        async def run_once(self):
            self.calls += 1
            if self.calls == 1:
                raise ValueError("lost lease")
            stop.set()
            return False

    assert hasattr(worker_module, "run_worker_loop"), "worker loop must isolate item failures"
    worker = FlakyWorker()

    await worker_module.run_worker_loop(worker, stop, poll_seconds=0.001)

    assert worker.calls == 2


@pytest.mark.asyncio
async def test_worker_schedules_transient_failure_for_retry(database, session) -> None:
    difference = await seed_difference(session, DifferenceType.ATTRIBUTE_CONFLICT)
    job = await AnalysisJobService(session, operator=OPERATOR).create_job(
        difference.task_id,
        idempotency_key="worker-retry-1",
    )
    await session.commit()
    worker = AnalysisWorker(
        database.session_factory,
        analyzer_factory=lambda worker_session, operator: AnalysisService(
            worker_session,
            agent=TransientAgent(),
            operator=operator,
        ),
        worker_id="worker-retry",
        retry_wait_seconds=60,
    )

    assert await worker.run_once() is True
    async with database.session_factory() as check_session:
        refreshed = await AnalysisJobRepository(check_session).get(job.id)
        item = await check_session.scalar(
            __import__("sqlalchemy")
            .select(AnalysisWorkItemRecord)
            .where(AnalysisWorkItemRecord.job_id == job.id)
        )
        assert refreshed is not None and item is not None
        assert refreshed.status == AnalysisJobStatus.RUNNING.value
        assert refreshed.completed == 0
        assert item.status == AnalysisWorkItemStatus.RETRY_WAIT.value
        assert item.attempt_count == 1


@pytest.mark.asyncio
async def test_worker_exhaustion_persists_chinese_manual_fallback(database, session) -> None:
    difference = await seed_difference(session, DifferenceType.ATTRIBUTE_CONFLICT)
    job = await AnalysisJobService(session, operator=OPERATOR).create_job(
        difference.task_id,
        idempotency_key="worker-fallback-1",
    )
    item = await session.scalar(
        __import__("sqlalchemy")
        .select(AnalysisWorkItemRecord)
        .where(AnalysisWorkItemRecord.job_id == job.id)
    )
    assert item is not None
    item.max_attempts = 1
    await session.commit()
    worker = AnalysisWorker(
        database.session_factory,
        analyzer_factory=lambda worker_session, operator: AnalysisService(
            worker_session,
            agent=TransientAgent(),
            operator=operator,
        ),
        worker_id="worker-fallback",
        retry_wait_seconds=0,
    )

    assert await worker.run_once() is True
    async with database.session_factory() as check_session:
        refreshed = await AnalysisJobRepository(check_session).get(job.id)
        work_item = await check_session.scalar(
            __import__("sqlalchemy")
            .select(AnalysisWorkItemRecord)
            .where(AnalysisWorkItemRecord.job_id == job.id)
        )
        assert refreshed is not None and work_item is not None
        assert refreshed.status == AnalysisJobStatus.COMPLETED.value
        assert refreshed.manual_required == 1
        assert work_item.status == AnalysisWorkItemStatus.MANUAL_REQUIRED.value
        assert work_item.result_id is not None


@pytest.mark.asyncio
async def test_worker_supersedes_item_when_difference_version_has_changed(
    database,
    session,
) -> None:
    difference = await seed_difference(session, DifferenceType.ATTRIBUTE_CONFLICT)
    job = await AnalysisJobService(session, operator=OPERATOR).create_job(
        difference.task_id,
        idempotency_key="worker-stale-version",
    )
    await session.execute(
        update(DifferenceRecord)
        .where(DifferenceRecord.id == difference.id)
        .values(version=difference.version + 1)
    )
    await session.commit()

    class UnexpectedAgent:
        async def analyze(self, request):
            raise AssertionError("stale work must not call the model")

    worker = AnalysisWorker(
        database.session_factory,
        analyzer_factory=lambda worker_session, operator: AnalysisService(
            worker_session,
            agent=UnexpectedAgent(),
            operator=operator,
        ),
        worker_id="worker-stale",
    )

    assert await worker.run_once() is True
    async with database.session_factory() as check_session:
        refreshed = await AnalysisJobRepository(check_session).get(job.id)
        item = await check_session.scalar(
            select(AnalysisWorkItemRecord).where(AnalysisWorkItemRecord.job_id == job.id)
        )
        assert refreshed is not None and item is not None
        assert refreshed.status == AnalysisJobStatus.COMPLETED_WITH_FAILURES.value
        assert refreshed.failed == 1
        assert item.status == AnalysisWorkItemStatus.SUPERSEDED.value
        assert item.result_id is None
