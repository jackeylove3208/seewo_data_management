import asyncio
import hashlib
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.agent_runtime.csv_governance_handlers import CsvGovernanceHandlers
from app.agent_runtime.local_publication import publish_local_target
from app.agent_runtime.repository import AgentRuntimeRepository
from app.agent_runtime.service import AgentSupervisorService
from app.agent_runtime.state_machine import AgentPhase, AgentRunKind
from app.agent_runtime.worker import AgentWorkContext
from app.core.config import Settings
from app.core.security import OperatorContext
from app.models.executions import TargetVersionRecord
from app.models.reconciliation import ReconciliationTask
from app.models.snapshots import Snapshot, SourceFile
from app.repositories.executions import ExecutionRepository


def _hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


@pytest.mark.asyncio
async def test_local_target_initial_version_is_copied_to_managed_storage(
    database,
    tmp_path: Path,
) -> None:
    authority = tmp_path / "sources" / "data" / "authority.csv"
    target = tmp_path / "sources" / "seewo" / "target.csv"
    authority.parent.mkdir(parents=True)
    target.parent.mkdir(parents=True)
    authority.write_text("编号,姓名\n001,权威姓名\n", encoding="utf-8")
    target.write_text("编号,姓名\n001,旧姓名\n", encoding="utf-8")
    output_root = tmp_path / "managed-targets"

    async with database.session_factory() as session:
        task = ReconciliationTask(
            tenant_id="school-1",
            scope_id="all",
            snapshot_mode="full",
            entity_types=["student"],
            workflow_version="agent-graph-v1",
            title="本地写回任务",
            agent_intent={
                "source": {"kind": "local", "source_ref": "data/authority.csv"},
                "target": {"kind": "local", "source_ref": "seewo/target.csv"},
            },
            idempotency_key=f"managed-initial-{uuid4()}",
            request_hash="a" * 64,
        )
        session.add(task)
        await session.flush()
        for role, path in (("authoritative", authority), ("target", target)):
            digest = _hash(path)
            source_file = SourceFile(
                task_id=task.id,
                source_role=role,
                original_name=path.name,
                storage_name=f"local-{uuid4().hex}",
                storage_path=str(path),
                sha256=digest,
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
                    file_hash=digest,
                    content_hash=digest,
                    summary={},
                )
            )
        run = await AgentSupervisorService(
            session,
            operator=OperatorContext(operator_id="operator-1", tenant_id="school-1"),
        ).start(task_id=task.id, conversation_id=None)
        await session.commit()

    async with database.session_factory() as session:
        await CsvGovernanceHandlers(output_root=output_root).aggregate(
            session,
            AgentWorkContext(
                worker_id="test-worker",
                run_id=run.id,
                task_id=task.id,
                tenant_id="school-1",
                phase=AgentPhase.AGGREGATE_RISK_AND_APPROVALS,
                attempt_count=1,
                lease_token=uuid4(),
            ),
        )
        version = await session.scalar(
            select(TargetVersionRecord).where(TargetVersionRecord.task_id == task.id)
        )
        assert version is not None
        managed_path = Path(version.storage_path)
        assert managed_path != target
        assert managed_path.is_relative_to(output_root)
        assert await asyncio.to_thread(
            managed_path.read_text, encoding="utf-8"
        ) == await asyncio.to_thread(target.read_text, encoding="utf-8")

        await asyncio.to_thread(
            target.write_text,
            "编号,姓名\n001,外部修改\n",
            encoding="utf-8",
        )
        assert (
            await asyncio.to_thread(managed_path.read_text, encoding="utf-8")
            == "编号,姓名\n001,旧姓名\n"
        )


@pytest.mark.asyncio
async def test_verified_local_target_version_is_published_and_checkpointed(
    database,
    tmp_path: Path,
) -> None:
    root = tmp_path / "sources"
    authority = root / "data" / "authority.csv"
    target = root / "seewo" / "target.csv"
    authority.parent.mkdir(parents=True)
    target.parent.mkdir(parents=True)
    authority.write_text("编号,姓名\n001,权威姓名\n", encoding="utf-8")
    target.write_text("编号,姓名\n001,旧姓名\n", encoding="utf-8")
    managed = tmp_path / "managed" / "verified.csv"
    managed.parent.mkdir()
    managed.write_text("编号,姓名\n001,权威姓名\n", encoding="utf-8")

    async with database.session_factory() as session:
        task = ReconciliationTask(
            tenant_id="school-1",
            scope_id="all",
            snapshot_mode="full",
            entity_types=["student"],
            workflow_version="agent-graph-v1",
            title="本地发布任务",
            agent_intent={
                "source": {"kind": "local", "source_ref": "data/authority.csv"},
                "target": {"kind": "local", "source_ref": "seewo/target.csv"},
            },
            idempotency_key=f"local-publish-{uuid4()}",
            request_hash="b" * 64,
        )
        session.add(task)
        await session.flush()
        target_snapshot: Snapshot | None = None
        for role, path in (("authoritative", authority), ("target", target)):
            digest = _hash(path)
            source_file = SourceFile(
                task_id=task.id,
                source_role=role,
                original_name=path.name,
                storage_name=f"local-{uuid4().hex}",
                storage_path=str(path),
                sha256=digest,
                size_bytes=path.stat().st_size,
            )
            session.add(source_file)
            await session.flush()
            snapshot = Snapshot(
                id=uuid4(),
                task_id=task.id,
                source_file_id=source_file.id,
                source_role=role,
                schema_version="agent-contract-v1",
                mapping_version="agent-contract-v1",
                file_hash=digest,
                content_hash=digest,
                summary={},
            )
            session.add(snapshot)
            if role == "target":
                target_snapshot = snapshot
        assert target_snapshot is not None
        run = await AgentSupervisorService(
            session,
            operator=OperatorContext(operator_id="operator-1", tenant_id="school-1"),
        ).start(task_id=task.id, conversation_id=None)
        version = await ExecutionRepository(session).create_target_version(
            task_id=task.id,
            tenant_id=task.tenant_id,
            source_snapshot_id=target_snapshot.id,
            parent_version_id=None,
            batch_id=None,
            file_sha256=_hash(managed),
            content_hash="c" * 64,
            storage_path=managed,
        )
        await session.commit()

    settings = Settings(
        agent_local_read_roots=(root,),
        agent_local_write_roots=(root / "seewo",),
        _env_file=None,
    )
    async with database.session_factory() as session:
        result = await publish_local_target(
            session,
            settings=settings,
            task_id=task.id,
            run_id=run.id,
            phase=AgentPhase.GENERATE_REPORT,
            target_version_id=version.id,
        )
        await session.commit()

    assert result["status"] == "published"
    assert result["source_ref"] == "seewo/target.csv"
    assert await asyncio.to_thread(target.read_text, encoding="utf-8") == (
        "编号,姓名\n001,权威姓名\n"
    )
    async with database.session_factory() as session:
        checkpoint = await AgentRuntimeRepository(session).get_checkpoint(
            run.id,
            phase=AgentPhase.GENERATE_REPORT,
            checkpoint_key="agent-local-publication-v1",
        )
        assert checkpoint is not None
        assert checkpoint.payload["published_sha256"] == _hash(target)


@pytest.mark.asyncio
async def test_local_publication_with_no_verified_version_does_not_replace_target(
    database,
    tmp_path: Path,
) -> None:
    root = tmp_path / "sources"
    target = root / "seewo" / "target.csv"
    target.parent.mkdir(parents=True)
    target.write_text("编号,姓名\n001,旧姓名\n", encoding="utf-8")
    task = ReconciliationTask(
        tenant_id="school-1",
        scope_id="all",
        snapshot_mode="full",
        entity_types=["student"],
        workflow_version="agent-graph-v1",
        agent_intent={
            "source": {"kind": "local", "source_ref": "data/authority.csv"},
            "target": {"kind": "local", "source_ref": "seewo/target.csv"},
        },
        idempotency_key=f"local-no-publish-{uuid4()}",
        request_hash="d" * 64,
    )
    async with database.session_factory() as session:
        session.add(task)
        await session.flush()
        run = await AgentRuntimeRepository(session).create_run(
            task_id=task.id,
            tenant_id=task.tenant_id,
            conversation_id=None,
            kind=AgentRunKind.SYNC,
            workflow_version=task.workflow_version,
        )
        await session.commit()

    before = await asyncio.to_thread(target.read_bytes)
    async with database.session_factory() as session:
        result = await publish_local_target(
            session,
            settings=Settings(
                agent_local_read_roots=(root,),
                agent_local_write_roots=(root / "seewo",),
                _env_file=None,
            ),
            task_id=task.id,
            run_id=run.id,
            phase=AgentPhase.GENERATE_REPORT,
            target_version_id=None,
        )
        await session.commit()

    assert result["status"] == "no_changes"
    assert await asyncio.to_thread(target.read_bytes) == before
