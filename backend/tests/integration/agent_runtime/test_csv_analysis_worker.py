import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.agent_runtime.csv_analysis_worker import CsvAnalysisHandlerFactory
from app.agent_runtime.service import AgentSupervisorService
from app.agent_runtime.state_machine import AgentPhase, AgentRunStatus
from app.agent_runtime.worker import AgentWorker
from app.ai.providers.base import LLMRequest, LLMResponse
from app.core.security import OperatorContext
from app.models.agent_analysis import AgentFindingRecord
from app.models.agent_runtime import AgentRunRecord
from app.models.reconciliation import ReconciliationTask
from app.models.snapshots import Snapshot, SourceFile


def test_csv_analysis_worker_exposes_only_csv_analysis_phases(database) -> None:
    handlers = CsvAnalysisHandlerFactory(
        database.session_factory, tokenization_secret="s" * 16
    ).handlers()

    assert set(handlers) == {
        AgentPhase.INGEST_AND_NORMALIZE,
        AgentPhase.BUILD_IDENTITY_WORK,
        AgentPhase.ANALYZE_BATCHES,
        AgentPhase.CLARIFY_IDENTITY_CONFLICTS,
    }


class _ExtraRowProvider:
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
                        "category_zh": "希沃多余",
                        "analysis_zh": "第三方无对应身份键。",
                        "evidence_refs": [f"input:{item['locator']}"],
                        "solutions": [
                            {
                                "operation": "delete",
                                "risk": "high",
                                "solution_zh": "删除希沃多余记录。",
                                "recommended": True,
                            }
                        ],
                    }
                    for item in evidence
                ]
            },
        )


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
