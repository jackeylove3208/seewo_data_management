import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select

from app.agent_reporting.service import AgentReportingService
from app.agent_runtime.csv_analysis_worker import CsvAnalysisHandlerFactory
from app.agent_runtime.repository import AgentRuntimeRepository
from app.agent_runtime.service import AgentSupervisorService
from app.agent_runtime.state_machine import AgentPhase, AgentRunStatus
from app.agent_runtime.worker import AgentWorker
from app.ai.providers.base import LLMRequest, LLMResponse, TransientModelError
from app.core.config import Settings
from app.core.database import Database
from app.core.security import OperatorContext
from app.models import Base
from app.models.agent_analysis import (
    AgentApprovalGroupRecord,
    AgentFindingRecord,
    AgentGovernanceOperationRecord,
    AgentModelBatchRecord,
)
from app.models.agent_runtime import AgentRunRecord, AgentTaskEventRecord
from app.models.reconciliation import ReconciliationTask
from app.models.reporting import AgentReportRecord
from app.models.snapshots import Snapshot, SourceFile
from app.repositories.agent_analysis import AgentAnalysisRepository
from app.repositories.agent_governance import AgentGovernanceRepository


def test_csv_agent_worker_exposes_the_complete_sync_pipeline(database) -> None:
    handlers = CsvAnalysisHandlerFactory(
        database.session_factory, tokenization_secret="s" * 16
    ).handlers()

    assert set(handlers) == {
        AgentPhase.INGEST_AND_NORMALIZE,
        AgentPhase.BUILD_IDENTITY_WORK,
        AgentPhase.ANALYZE_BATCHES,
        AgentPhase.CLARIFY_IDENTITY_CONFLICTS,
        AgentPhase.AGGREGATE_RISK_AND_APPROVALS,
        AgentPhase.COMPILE_EXECUTION_PLAN,
        AgentPhase.EXECUTE_AND_VERIFY,
        AgentPhase.GENERATE_REPORT,
        AgentPhase.PLAN_RESTORE,
        AgentPhase.CLARIFY_RESTORE_CONFLICTS,
        AgentPhase.APPROVE_RESTORE,
        AgentPhase.EXECUTE_RESTORE,
        AgentPhase.REPORT_RESTORE,
    }


class _ExtraRowProvider:
    def __init__(self) -> None:
        self.attempts = 0

    async def complete_json_once(self, request: LLMRequest) -> LLMResponse:
        self.attempts += 1
        evidence = json.loads(request.messages[-1].content)["untrusted_evidence"]
        return LLMResponse(
            provider="stub",
            model="stub",
            output={
                "findings": [
                    {
                        "work_item_id": item["work_item_id"],
                        "kind": item["kind"],
                        "category_zh": "希沃多余",
                        "analysis_zh": "第三方无对应身份键。",
                        "evidence_refs": [f"input:{item['locator']}"],
                        "solutions": [
                            {
                                "operation": (
                                    "create" if item["kind"] == "target_missing" else "delete"
                                ),
                                "risk": "high" if item["kind"] == "target_extra" else "medium",
                                "solution_zh": (
                                    "在希沃创建权威记录。"
                                    if item["kind"] == "target_missing"
                                    else "删除希沃多余记录。"
                                ),
                                "recommended": True,
                            }
                        ],
                    }
                    for item in evidence
                ]
            },
        )


class _TimeoutProvider:
    def __init__(self) -> None:
        self.attempts = 0

    async def complete_json_once(self, request: LLMRequest) -> LLMResponse:
        del request
        self.attempts += 1
        try:
            raise httpx.ReadTimeout("analysis request timed out")
        except httpx.ReadTimeout as error:
            raise TransientModelError("model transport failed") from error


class _BlockingTimeoutProvider(_TimeoutProvider):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def complete_json_once(self, request: LLMRequest) -> LLMResponse:
        if self.attempts == 0:
            self.started.set()
            await self.release.wait()
        return await super().complete_json_once(request)


@pytest.fixture
async def file_database(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'agent-runtime.db'}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield database
    finally:
        await database.dispose()


@pytest.mark.asyncio
async def test_model_timeout_attempts_are_visible_before_task_is_blocked(
    file_database: Database, tmp_path: Path
) -> None:
    authority = tmp_path / "timeout-authority.csv"
    authority.write_text(
        "类别,姓名,编号,班级,电话,邮箱\n学生,李四,S-1,一班,13800138000,s@example.test\n",
        encoding="utf-8",
    )
    target = tmp_path / "timeout-target.csv"
    target.write_text(
        "类别,姓名,编号,班级,电话,邮箱\n学生,王五,S-9,一班,13800138009,x@example.test\n",
        encoding="utf-8",
    )
    async with file_database.session_factory() as session:
        task = ReconciliationTask(
            tenant_id="school-1",
            scope_id="all",
            snapshot_mode="full",
            entity_types=["student"],
            workflow_version="new-agent-v1",
            idempotency_key=f"csv-timeout-worker-{uuid4()}",
            request_hash="f" * 64,
        )
        session.add(task)
        await session.flush()
        for role, path in (("authoritative", authority), ("target", target)):
            file_sha256 = await asyncio.to_thread(
                lambda path=path: hashlib.sha256(path.read_bytes()).hexdigest()
            )
            source = SourceFile(
                task_id=task.id,
                source_role=role,
                original_name=path.name,
                storage_name=f"{uuid4()}.csv",
                storage_path=str(path),
                sha256=file_sha256,
                size_bytes=path.stat().st_size,
            )
            session.add(source)
            await session.flush()
            session.add(
                Snapshot(
                    id=uuid4(),
                    task_id=task.id,
                    source_file_id=source.id,
                    source_role=role,
                    schema_version="agent-contract-v1",
                    mapping_version="agent-contract-v1",
                    file_hash=source.sha256,
                    content_hash=uuid4().hex * 2,
                    summary={},
                )
            )
        run = await AgentSupervisorService(
            session,
            operator=OperatorContext(operator_id="operator-1", tenant_id="school-1"),
        ).start(task_id=task.id, conversation_id=None)
        run_id = run.id
        await session.commit()

    provider = _BlockingTimeoutProvider()
    worker = AgentWorker(
        file_database.session_factory,
        worker_id="csv-timeout-worker",
        lease_seconds=60,
        handlers=CsvAnalysisHandlerFactory(
            file_database.session_factory,
            tokenization_secret="s" * 16,
            provider=provider,
        ).handlers(),
    )
    assert await worker.run_once() is True
    assert await worker.run_once() is True
    async with file_database.session_factory() as session:
        async with session.begin():
            seeded_run = await AgentRuntimeRepository(session).claim_next_run(
                worker_id="csv-timeout-seed-worker",
                lease_seconds=60,
                phases=frozenset({AgentPhase.ANALYZE_BATCHES}),
            )
            assert seeded_run is not None and seeded_run.lease_token is not None
            batch = await session.scalar(
                select(AgentModelBatchRecord).where(
                    AgentModelBatchRecord.run_id == run_id
                )
            )
            assert batch is not None
            repository = AgentAnalysisRepository(session)
            runtime = AgentRuntimeRepository(session)
            for attempt_number in (1, 2):
                claim = await repository.claim_batch(
                    batch.id,
                    worker_id="csv-timeout-seed-worker",
                    run_lease_token=seeded_run.lease_token,
                    lease_seconds=60,
                )
                assert claim is not None and claim.lease_token is not None
                await runtime.append_event(
                    run_id,
                    "model_attempt_started",
                    {"attempt": attempt_number, "attempt_count": 4},
                )
                failed = await repository.append_failed_attempt(
                    batch_id=batch.id,
                    worker_id="csv-timeout-seed-worker",
                    run_lease_token=seeded_run.lease_token,
                    lease_token=claim.lease_token,
                    provider="configured",
                    model="configured",
                    skill_name="reconcile-entity-batch",
                    skill_version="agent-contract-v1",
                    prompt_version="agent-csv-analysis-v1",
                    safe_error_code="model_timeout",
                )
                await runtime.append_event(
                    run_id,
                    "model_attempt_failed",
                    {
                        "attempt": failed.attempt_number,
                        "attempt_count": 4,
                        "failure_category": failed.safe_error_code,
                    },
                )
            await runtime.release_run_claim(
                run_id,
                worker_id="csv-timeout-seed-worker",
                lease_token=seeded_run.lease_token,
            )
    analysis_task = asyncio.create_task(worker.run_once())
    await asyncio.wait_for(provider.started.wait(), timeout=2)

    async with file_database.session_factory() as session:
        visible_starts = tuple(
            await session.scalars(
                select(AgentTaskEventRecord).where(
                    AgentTaskEventRecord.run_id == run_id,
                    AgentTaskEventRecord.event_type == "model_attempt_started",
                )
            )
        )
    assert [event.payload["attempt"] for event in visible_starts] == [1, 2, 3]

    provider.release.set()
    assert await analysis_task is True

    async with file_database.session_factory() as session:
        run = await session.get(AgentRunRecord, run_id)
        events = tuple(
            await session.scalars(
                select(AgentTaskEventRecord)
                .where(AgentTaskEventRecord.run_id == run_id)
                .order_by(AgentTaskEventRecord.sequence)
            )
        )

    assert provider.attempts == 2
    assert run is not None and run.status == AgentRunStatus.BLOCKED_MODEL_ERROR.value
    starts = [event for event in events if event.event_type == "model_attempt_started"]
    failures = [event for event in events if event.event_type == "model_attempt_failed"]
    assert [event.payload["attempt"] for event in starts] == [1, 2, 3, 4]
    assert [event.payload["attempt"] for event in failures] == [1, 2, 3, 4]
    assert {event.payload["failure_category"] for event in failures} == {"model_timeout"}
    exhausted = next(event for event in events if event.event_type == "model_retry_exhausted")
    assert exhausted.payload["attempt_count"] == 4


@pytest.mark.asyncio
async def test_repository_failure_does_not_trigger_model_retries_or_block_run(
    database,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = tmp_path / "repository-error-authority.csv"
    authority.write_text(
        "类别,姓名,编号,班级,电话,邮箱\n学生,李四,S-1,一班,13800138000,s@example.test\n",
        encoding="utf-8",
    )
    target = tmp_path / "repository-error-target.csv"
    target.write_text(
        "类别,姓名,编号,班级,电话,邮箱\n学生,王五,S-9,一班,13800138009,x@example.test\n",
        encoding="utf-8",
    )
    async with database.session_factory() as session:
        task = ReconciliationTask(
            tenant_id="school-1",
            scope_id="all",
            snapshot_mode="full",
            entity_types=["student"],
            workflow_version="new-agent-v1",
            idempotency_key=f"csv-repository-error-{uuid4()}",
            request_hash="e" * 64,
        )
        session.add(task)
        await session.flush()
        for role, path in (("authoritative", authority), ("target", target)):
            source = SourceFile(
                task_id=task.id,
                source_role=role,
                original_name=path.name,
                storage_name=f"{uuid4()}.csv",
                storage_path=str(path),
                sha256=uuid4().hex * 2,
                size_bytes=path.stat().st_size,
            )
            session.add(source)
            await session.flush()
            session.add(
                Snapshot(
                    id=uuid4(),
                    task_id=task.id,
                    source_file_id=source.id,
                    source_role=role,
                    schema_version="agent-contract-v1",
                    mapping_version="agent-contract-v1",
                    file_hash=source.sha256,
                    content_hash=uuid4().hex * 2,
                    summary={},
                )
            )
        run = await AgentSupervisorService(
            session,
            operator=OperatorContext(operator_id="operator-1", tenant_id="school-1"),
        ).start(task_id=task.id, conversation_id=None)
        run_id = run.id
        await session.commit()

    async def fail_finalize(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(AgentAnalysisRepository, "finalize_batch", fail_finalize)
    provider = _ExtraRowProvider()
    worker = AgentWorker(
        database.session_factory,
        worker_id="csv-repository-error-worker",
        lease_seconds=60,
        handlers=CsvAnalysisHandlerFactory(
            database.session_factory,
            tokenization_secret="s" * 16,
            provider=provider,
        ).handlers(),
    )
    assert await worker.run_once() is True
    assert await worker.run_once() is True
    with pytest.raises(RuntimeError, match="database unavailable"):
        await worker.run_once()

    async with database.session_factory() as session:
        run = await session.get(AgentRunRecord, run_id)
        batch = await session.scalar(
            select(AgentModelBatchRecord).where(
                AgentModelBatchRecord.run_id == run_id
            )
        )
        exhausted = await session.scalar(
            select(AgentTaskEventRecord).where(
                AgentTaskEventRecord.run_id == run_id,
                AgentTaskEventRecord.event_type == "model_retry_exhausted",
            )
        )

    assert run is not None and run.status == AgentRunStatus.RUNNING.value
    assert batch is not None and batch.status == "pending"
    assert provider.attempts == 1
    assert exhausted is None


@pytest.mark.asyncio
async def test_worker_runs_csv_analysis_only_pipeline_without_target_mutation(
    database, tmp_path: Path
) -> None:
    authority = tmp_path / "authority.csv"
    authority.write_text(
        "category,name,number,class,phone,email\nstudent,李四,S-1,一班,13800138000,s@example.test\n",
        encoding="utf-8",
    )
    target = tmp_path / "target.csv"
    target.write_text(
        "category,name,number,class,phone,email\nstudent,王五,S-9,一班,13800138009,x@example.test\n",
        encoding="utf-8",
    )
    async with database.session_factory() as session:
        task = ReconciliationTask(
            tenant_id="school-1",
            scope_id="all",
            snapshot_mode="full",
            entity_types=["student"],
            workflow_version="new-agent-v1",
            idempotency_key=f"csv-worker-{uuid4()}",
            request_hash="a" * 64,
        )
        session.add(task)
        await session.flush()
        for role, path in (("authoritative", authority), ("target", target)):
            source = SourceFile(
                task_id=task.id,
                source_role=role,
                original_name=path.name,
                storage_name=f"{uuid4()}.csv",
                storage_path=str(path),
                sha256=uuid4().hex * 2,
                size_bytes=path.stat().st_size,
            )
            session.add(source)
            await session.flush()
            session.add(
                Snapshot(
                    id=uuid4(),
                    task_id=task.id,
                    source_file_id=source.id,
                    source_role=role,
                    schema_version="agent-contract-v1",
                    mapping_version="agent-contract-v1",
                    file_hash=source.sha256,
                    content_hash=uuid4().hex * 2,
                    summary={},
                )
            )
        await session.flush()
        run = await AgentSupervisorService(
            session,
            operator=OperatorContext(operator_id="operator-1", tenant_id="school-1"),
        ).start(task_id=task.id, conversation_id=None)
        run_id = run.id
        await session.commit()

    factory = CsvAnalysisHandlerFactory(
        database.session_factory,
        tokenization_secret="s" * 16,
        provider=_ExtraRowProvider(),
    )
    worker = AgentWorker(
        database.session_factory,
        worker_id="csv-worker-1",
        lease_seconds=60,
        handlers=factory.handlers(),
    )
    assert await worker.run_once() is True
    assert await worker.run_once() is True
    assert await worker.run_once() is True
    async with database.session_factory() as session:
        run = await session.get(AgentRunRecord, run_id)
        assert run is not None
        assert run.phase == AgentPhase.ANALYZE_BATCHES.value
        assert run.status == AgentRunStatus.WAITING_HUMAN.value
        assert len(tuple(await session.scalars(select(AgentFindingRecord)))) == 2
        succeeded_events = tuple(
            await session.scalars(
                select(AgentTaskEventRecord).where(
                    AgentTaskEventRecord.run_id == run_id,
                    AgentTaskEventRecord.event_type == "model_attempt_succeeded",
                )
            )
        )
        assert [event.payload["attempt"] for event in succeeded_events] == [1]


class _FieldDifferenceProvider:
    async def complete_json_once(self, request: LLMRequest) -> LLMResponse:
        evidence = json.loads(request.messages[-1].content)["untrusted_evidence"]
        return LLMResponse(
            provider="stub",
            model="stub",
            output={
                "findings": [
                    {
                        "work_item_id": item["work_item_id"],
                        "kind": item["kind"],
                        "category_zh": "姓名不一致",
                        "analysis_zh": "身份键一致，但希沃姓名与权威数据不同。",
                        "evidence_refs": [f"input:{item['locator']}"],
                        "solutions": [
                            {
                                "operation": "update",
                                "risk": "medium",
                                "solution_zh": "按第三方权威姓名更新希沃数据。",
                                "recommended": True,
                            }
                        ],
                    }
                    for item in evidence
                ]
            },
        )


@pytest.mark.asyncio
async def test_complete_csv_agent_pipeline_executes_verified_change_and_reports(
    database, tmp_path: Path
) -> None:
    local_root = tmp_path / "local-sources"
    authority = local_root / "data" / "authority-full.csv"
    target = local_root / "seewo" / "target-full.csv"
    authority.parent.mkdir(parents=True)
    target.parent.mkdir(parents=True)
    authority.write_text(
        "类别,姓名,编号,班级,电话,邮箱\n学生,李四,S-1,一班,13800138000,s@example.test\n",
        encoding="utf-8",
    )
    target.write_text(
        "类别,姓名,编号,班级,电话,邮箱\n学生,错名,S-1,一班,13800138000,s@example.test\n",
        encoding="utf-8",
    )
    async with database.session_factory() as session:
        task = ReconciliationTask(
            tenant_id="school-1",
            scope_id="all",
            snapshot_mode="full",
            entity_types=["student"],
            workflow_version="new-agent-v1",
            agent_intent={
                "source": {"kind": "local", "source_ref": "data/authority-full.csv"},
                "target": {"kind": "local", "source_ref": "seewo/target-full.csv"},
            },
            idempotency_key=f"csv-full-worker-{uuid4()}",
            request_hash="b" * 64,
        )
        session.add(task)
        await session.flush()
        for role, path in (("authoritative", authority), ("target", target)):
            file_sha256 = await asyncio.to_thread(
                lambda path=path: hashlib.sha256(path.read_bytes()).hexdigest()
            )
            source = SourceFile(
                task_id=task.id,
                source_role=role,
                original_name=path.name,
                storage_name=f"{uuid4()}.csv",
                storage_path=str(path),
                sha256=file_sha256,
                size_bytes=path.stat().st_size,
            )
            session.add(source)
            await session.flush()
            session.add(
                Snapshot(
                    id=uuid4(),
                    task_id=task.id,
                    source_file_id=source.id,
                    source_role=role,
                    schema_version="agent-contract-v1",
                    mapping_version="agent-contract-v1",
                    file_hash=source.sha256,
                    content_hash=uuid4().hex * 2,
                    summary={},
                )
            )
        run = await AgentSupervisorService(
            session,
            operator=OperatorContext(operator_id="operator-1", tenant_id="school-1"),
        ).start(task_id=task.id, conversation_id=None)
        run_id = run.id
        task_id = task.id
        await session.commit()

    factory = CsvAnalysisHandlerFactory(
        database.session_factory,
        tokenization_secret="s" * 16,
        provider=_FieldDifferenceProvider(),
        analysis_only=False,
        csv_execution_enabled=True,
        output_root=tmp_path / "agent-outputs",
        settings=Settings(
            agent_local_read_roots=(local_root,),
            agent_local_write_roots=(local_root / "seewo",),
            _env_file=None,
        ),
    )
    worker = AgentWorker(
        database.session_factory,
        worker_id="csv-full-worker",
        lease_seconds=60,
        handlers=factory.handlers(),
    )
    for _step in range(5):
        assert await worker.run_once() is True

    async with database.session_factory() as session:
        run = await session.get(AgentRunRecord, run_id)
        groups = tuple(
            await session.scalars(
                select(AgentApprovalGroupRecord).where(
                    AgentApprovalGroupRecord.run_id == run_id,
                    AgentApprovalGroupRecord.status == "pending",
                )
            )
        )
        assert run is not None and run.status == AgentRunStatus.WAITING_HUMAN.value
        assert groups
        for group in groups:
            await AgentGovernanceRepository(session).decide_approval(
                group.id,
                membership_hash=group.membership_hash,
                approved=True,
                actor_id="operator-1",
                reason="approved in test",
            )
        await AgentRuntimeRepository(session).transition_run(
            run.id, requested_status=AgentRunStatus.RUNNING
        )
        await session.commit()

    for _step in range(4):
        assert await worker.run_once() is True

    async with database.session_factory() as session:
        run = await session.get(AgentRunRecord, run_id)
        report = await session.scalar(
            select(AgentReportRecord).where(AgentReportRecord.task_id == task_id)
        )
        operations = tuple(
            await session.scalars(
                select(AgentGovernanceOperationRecord).where(
                    AgentGovernanceOperationRecord.task_id == task_id
                )
            )
        )
        assert run is not None and run.status == AgentRunStatus.COMPLETED.value
        assert run.phase == AgentPhase.TERMINAL.value
        assert report is not None and report.rollback_eligible is True
        assert report.facts["findings"] == [
            {
                "id": report.facts["findings"][0]["id"],
                "kind": "field_difference",
                "category_zh": "姓名不一致",
                "entity_kind": "student",
                "entity_name": "李四",
                "entity_number": "S-1",
                "class_name": "一班",
                "source_locator": "csv:2",
                "analysis_zh": "身份键一致，但希沃姓名与权威数据不同。",
                "solution_zh": "按第三方权威姓名更新希沃数据。",
                "recommended_operation": "update",
                "risk": "medium",
                "operator_decision": "approved",
                "execution_status": "succeeded",
            }
        ]
        assert len(operations) == 1
        assert operations[0].status == "succeeded"
        output_path = Path(report.facts["output_target_path"])
        assert await asyncio.to_thread(output_path.exists)
        content = await asyncio.to_thread(output_path.read_text, encoding="utf-8")
        assert "学生,李四,S-1" in content
        local_content = await asyncio.to_thread(target.read_text, encoding="utf-8")
        assert "学生,李四,S-1" in local_content

        rollback_preview = await AgentReportingService(session).create_rollback_task(
            source_task_id=task_id,
            tenant_id="school-1",
            requested_by="operator-1",
            target_version_id=UUID(report.facts["output_target_version_id"]),
        )
        rollback_task_id = rollback_preview.task_id
        await AgentSupervisorService(
            session,
            operator=OperatorContext(operator_id="operator-1", tenant_id="school-1"),
        ).confirm_rollback(task_id=rollback_task_id)
        await session.commit()

    for _step in range(5):
        assert await worker.run_once() is True
    async with database.session_factory() as session:
        rollback_report = await session.scalar(
            select(AgentReportRecord).where(
                AgentReportRecord.task_id == rollback_task_id
            )
        )
        assert rollback_report is not None
        assert rollback_report.kind == "rollback"
        rollback_path = Path(rollback_report.facts["output_target_path"])
        rollback_content = await asyncio.to_thread(
            rollback_path.read_text, encoding="utf-8"
        )
        assert "学生,错名,S-1" in rollback_content
        local_content = await asyncio.to_thread(target.read_text, encoding="utf-8")
        assert "学生,错名,S-1" in local_content


@pytest.mark.asyncio
async def test_unrecognizable_csv_routes_directly_to_non_rollback_abnormal_report(
    database, tmp_path: Path
) -> None:
    authority = tmp_path / "bad-authority.csv"
    authority.write_text("foo,bar\n1,2\n", encoding="utf-8")
    target = tmp_path / "unused-target.csv"
    target.write_text("类别,编号\n学生,S-1\n", encoding="utf-8")
    async with database.session_factory() as session:
        task = ReconciliationTask(
            tenant_id="school-1",
            scope_id="all",
            snapshot_mode="full",
            entity_types=["student"],
            workflow_version="new-agent-v1",
            idempotency_key=f"csv-abnormal-{uuid4()}",
            request_hash="c" * 64,
        )
        session.add(task)
        await session.flush()
        for role, path in (("authoritative", authority), ("target", target)):
            source_file = SourceFile(
                task_id=task.id,
                source_role=role,
                original_name=path.name,
                storage_name=f"{uuid4()}.csv",
                storage_path=str(path),
                sha256=uuid4().hex * 2,
                size_bytes=path.stat().st_size,
            )
            session.add(source_file)
            await session.flush()
            session.add(
                Snapshot(
                    id=uuid4(),
                    task_id=task.id,
                    source_file_id=source_file.id,
                    source_role=role,
                    schema_version="agent-contract-v1",
                    mapping_version="agent-contract-v1",
                    file_hash=source_file.sha256,
                    content_hash=uuid4().hex * 2,
                    summary={},
                )
            )
        run = await AgentSupervisorService(
            session,
            operator=OperatorContext(operator_id="operator-1", tenant_id="school-1"),
        ).start(task_id=task.id, conversation_id=None)
        run_id, task_id = run.id, task.id
        await session.commit()

    worker = AgentWorker(
        database.session_factory,
        worker_id="csv-abnormal-worker",
        lease_seconds=60,
        handlers=CsvAnalysisHandlerFactory(
            database.session_factory,
            tokenization_secret="s" * 16,
            provider=_FieldDifferenceProvider(),
            analysis_only=False,
        ).handlers(),
    )
    assert await worker.run_once() is True
    assert await worker.run_once() is True

    async with database.session_factory() as session:
        run = await session.get(AgentRunRecord, run_id)
        report = await session.scalar(
            select(AgentReportRecord).where(AgentReportRecord.task_id == task_id)
        )
        assert run is not None and run.status == AgentRunStatus.COMPLETED.value
        assert report is not None
        assert report.terminal_state == "abnormal_input"
        assert report.rollback_eligible is False


@pytest.mark.asyncio
async def test_high_risk_approval_resumes_worker_and_independent_work_continues(
    database, tmp_path: Path
) -> None:
    authority = tmp_path / "authority-approval.csv"
    authority.write_text(
        "类别,姓名,编号,班级,电话,邮箱\n学生,李四,S-1,一班,13800138000,s@example.test\n",
        encoding="utf-8",
    )
    target = tmp_path / "target-approval.csv"
    target.write_text(
        "类别,姓名,编号,班级,电话,邮箱\n学生,王五,S-9,一班,13800138009,x@example.test\n",
        encoding="utf-8",
    )
    async with database.session_factory() as session:
        task = ReconciliationTask(
            tenant_id="school-1",
            scope_id="all",
            snapshot_mode="full",
            entity_types=["student"],
            workflow_version="new-agent-v1",
            idempotency_key=f"csv-approval-{uuid4()}",
            request_hash="d" * 64,
        )
        session.add(task)
        await session.flush()
        for role, path in (("authoritative", authority), ("target", target)):
            source_file = SourceFile(
                task_id=task.id,
                source_role=role,
                original_name=path.name,
                storage_name=f"{uuid4()}.csv",
                storage_path=str(path),
                sha256=uuid4().hex * 2,
                size_bytes=path.stat().st_size,
            )
            session.add(source_file)
            await session.flush()
            session.add(
                Snapshot(
                    id=uuid4(),
                    task_id=task.id,
                    source_file_id=source_file.id,
                    source_role=role,
                    schema_version="agent-contract-v1",
                    mapping_version="agent-contract-v1",
                    file_hash=source_file.sha256,
                    content_hash=uuid4().hex * 2,
                    summary={},
                )
            )
        run = await AgentSupervisorService(
            session,
            operator=OperatorContext(operator_id="operator-1", tenant_id="school-1"),
        ).start(task_id=task.id, conversation_id=None)
        run_id = run.id
        await session.commit()

    worker = AgentWorker(
        database.session_factory,
        worker_id="csv-approval-worker",
        lease_seconds=60,
        handlers=CsvAnalysisHandlerFactory(
            database.session_factory,
            tokenization_secret="s" * 16,
            provider=_ExtraRowProvider(),
            analysis_only=False,
            csv_execution_enabled=True,
            output_root=tmp_path / "approval-outputs",
        ).handlers(),
    )
    for _step in range(5):
        assert await worker.run_once() is True
    async with database.session_factory() as session:
        run = await session.get(AgentRunRecord, run_id)
        groups = tuple(
            await session.scalars(
            select(AgentApprovalGroupRecord).where(
                AgentApprovalGroupRecord.run_id == run_id,
                AgentApprovalGroupRecord.status == "pending",
            )
            )
        )
        assert run is not None and run.status == AgentRunStatus.WAITING_HUMAN.value
        assert groups
        for group in groups:
            await AgentGovernanceRepository(session).decide_approval(
                group.id,
                membership_hash=group.membership_hash,
                approved=True,
                actor_id="operator-1",
                reason="approved in test",
            )
        await AgentRuntimeRepository(session).transition_run(
            run.id, requested_status=AgentRunStatus.RUNNING
        )
        await session.commit()

    for _step in range(4):
        assert await worker.run_once() is True
    async with database.session_factory() as session:
        run = await session.get(AgentRunRecord, run_id)
        operations = tuple(
            await session.scalars(
                select(AgentGovernanceOperationRecord).where(
                    AgentGovernanceOperationRecord.run_id == run_id
                )
            )
        )
        assert run is not None and run.status == AgentRunStatus.COMPLETED.value
        assert {item.operation_type for item in operations} == {"create", "delete"}
        assert all(item.status == "succeeded" for item in operations)
