import pytest
from sqlalchemy import select

from app.agent_graph.repository import AgentGraphRepository
from app.agent_runtime.repository import AgentRuntimeRepository
from app.agent_runtime.service import AgentSupervisorService, _termination_mutations
from app.agent_runtime.state_machine import AgentPhase, AgentRunStatus
from app.core.config import Settings
from app.core.security import OperatorContext
from app.models.agent_runtime import AgentFailureRecord, SchoolTaskLockRecord
from app.models.reconciliation import ReconciliationTask


async def create_agent_task(session, *, tenant_id: str, key: str) -> ReconciliationTask:
    task = ReconciliationTask(
        tenant_id=tenant_id,
        scope_id="all",
        snapshot_mode="full",
        entity_types=["student"],
        workflow_version="new-agent-v1",
        idempotency_key=key,
        request_hash=key,
    )
    session.add(task)
    await session.flush()
    return task


def supervisor(session, tenant_id: str = "school-1") -> AgentSupervisorService:
    return AgentSupervisorService(
        session,
        operator=OperatorContext(operator_id="operator-1", tenant_id=tenant_id),
    )


@pytest.mark.asyncio
async def test_start_is_idempotent_and_holds_school_lock_before_ingestion(session) -> None:
    task = await create_agent_task(session, tenant_id="school-1", key="supervisor-1")
    service = supervisor(session)

    started = await service.start(
        task_id=task.id,
        conversation_id=None,
    )
    replay = await service.start(
        task_id=task.id,
        conversation_id=None,
    )

    assert replay.id == started.id
    assert started.phase == AgentPhase.INGEST_AND_NORMALIZE.value
    assert started.status == AgentRunStatus.RUNNING.value
    lock = await session.scalar(
        select(SchoolTaskLockRecord).where(SchoolTaskLockRecord.active.is_(True))
    )
    assert lock is not None
    assert lock.owner_run_id == started.id


@pytest.mark.asyncio
async def test_remote_task_starts_at_the_versioned_materialization_node(session) -> None:
    conversation = await AgentRuntimeRepository(session).create_conversation(
        tenant_id="school-1",
        created_by="operator-1",
    )
    task = ReconciliationTask(
        tenant_id="school-1",
        scope_id="all",
        snapshot_mode="full",
        entity_types=["student"],
        workflow_version="agent-graph-v1",
        agent_intent={
            "source": {
                "kind": "remote_csv",
                "remote_source_id": "00000000-0000-0000-0000-000000000001",
            },
            "target": {"kind": "local", "source_ref": "seewo/roster.csv"},
        },
        idempotency_key="remote-supervisor-v2",
        request_hash="remote-supervisor-v2",
    )
    session.add(task)
    await session.flush()

    run = await AgentSupervisorService(
        session,
        operator=OperatorContext(operator_id="operator-1", tenant_id="school-1"),
        settings=Settings(
            new_agent_enabled=True,
            agent_graph_enabled=True,
            source_ingestion_v2_enabled=True,
        ),
    ).start(task_id=task.id, conversation_id=conversation.id)
    graph = await AgentGraphRepository(session).get_run_state_for_agent_run(run.id)

    assert graph is not None
    assert graph.graph_version == "agent-sync-graph-v2"
    assert graph.current_node == "materialize_sources"
    assert graph.cursor == 2


@pytest.mark.asyncio
async def test_api_task_freezes_ingestion_v3_and_starts_at_materialization(session) -> None:
    conversation = await AgentRuntimeRepository(session).create_conversation(
        tenant_id="school-1",
        created_by="operator-1",
    )
    task = ReconciliationTask(
        tenant_id="school-1",
        scope_id="all",
        snapshot_mode="full",
        entity_types=["teacher"],
        workflow_version="agent-graph-v1",
        agent_intent={
            "source": {
                "kind": "api",
                "configuration_id": "ding-school",
            },
            "target": {
                "kind": "database",
                "configuration_id": "seewo-mysql",
            },
        },
        idempotency_key="api-supervisor-v3",
        request_hash="api-supervisor-v3",
    )
    session.add(task)
    await session.flush()

    run = await AgentSupervisorService(
        session,
        operator=OperatorContext(operator_id="operator-1", tenant_id="school-1"),
        settings=Settings(
            new_agent_enabled=True,
            agent_graph_enabled=True,
            source_ingestion_v3_enabled=True,
        ),
    ).start(task_id=task.id, conversation_id=conversation.id)
    graph = await AgentGraphRepository(session).get_run_state_for_agent_run(run.id)

    assert run.ingestion_contract_version == "source-ingestion-v3"
    assert run.execution_contract_version == "deterministic-execution-v2"
    assert graph is not None
    assert graph.graph_version == "agent-sync-graph-v2"
    assert graph.current_node == "materialize_sources"
    assert graph.cursor == 2


@pytest.mark.asyncio
async def test_existing_api_run_is_not_upgraded_when_ingestion_v3_is_enabled(session) -> None:
    task = ReconciliationTask(
        tenant_id="school-1",
        scope_id="all",
        snapshot_mode="full",
        entity_types=["teacher"],
        workflow_version="agent-graph-v1",
        agent_intent={
            "source": {"kind": "api", "configuration_id": "ding-school"},
            "target": {"kind": "database", "configuration_id": "seewo-mysql"},
        },
        idempotency_key="api-supervisor-frozen-v2",
        request_hash="api-supervisor-frozen-v2",
    )
    session.add(task)
    await session.flush()
    operator = OperatorContext(operator_id="operator-1", tenant_id="school-1")

    original = await AgentSupervisorService(
        session,
        operator=operator,
        settings=Settings(
            new_agent_enabled=True,
            agent_graph_enabled=True,
            source_ingestion_v2_enabled=True,
            source_ingestion_v3_enabled=False,
        ),
    ).start(task_id=task.id, conversation_id=None)
    replay = await AgentSupervisorService(
        session,
        operator=operator,
        settings=Settings(
            new_agent_enabled=True,
            agent_graph_enabled=True,
            source_ingestion_v2_enabled=True,
            source_ingestion_v3_enabled=True,
        ),
    ).start(task_id=task.id, conversation_id=None)

    assert replay.id == original.id
    assert replay.ingestion_contract_version == "source-ingestion-v2"


@pytest.mark.asyncio
async def test_ingestion_v3_flag_does_not_upgrade_a_non_api_task(session) -> None:
    task = ReconciliationTask(
        tenant_id="school-1",
        scope_id="all",
        snapshot_mode="full",
        entity_types=["teacher"],
        workflow_version="agent-graph-v1",
        agent_intent={
            "source": {"kind": "local", "source_ref": "third-party.csv"},
            "target": {"kind": "local", "source_ref": "seewo.csv"},
        },
        idempotency_key="non-api-supervisor-v3",
        request_hash="non-api-supervisor-v3",
    )
    session.add(task)
    await session.flush()

    run = await AgentSupervisorService(
        session,
        operator=OperatorContext(operator_id="operator-1", tenant_id="school-1"),
        settings=Settings(
            new_agent_enabled=True,
            agent_graph_enabled=True,
            source_ingestion_v3_enabled=True,
        ),
    ).start(task_id=task.id, conversation_id=None)

    assert run.ingestion_contract_version == "model-mediated-ingestion-v1"
    assert run.execution_contract_version == "model-mediated-execution-v1"


@pytest.mark.asyncio
async def test_model_exhaustion_blocks_run_without_releasing_school_lock(session) -> None:
    task = await create_agent_task(session, tenant_id="school-1", key="supervisor-2")
    service = supervisor(session)
    run = await service.start(
        task_id=task.id,
        conversation_id=None,
    )
    repository = AgentRuntimeRepository(session)
    run = await repository.transition_run(run.id, requested_phase=AgentPhase.BUILD_IDENTITY_WORK)
    run = await repository.transition_run(run.id, requested_phase=AgentPhase.ANALYZE_BATCHES)

    blocked = await service.block_model_failure(
        run_id=run.id,
        reason="retries_exhausted",
        attempt_count=4,
        gateway_request_id="api-key-secret\n13800138000",
        unsafe_provider_detail="student phone 13800138000 api-key-secret",
    )

    assert blocked.status == AgentRunStatus.BLOCKED_MODEL_ERROR.value
    events = await repository.list_events(run.id)
    assert events[-1].payload["allowed_commands"] == ["terminate"]
    assert "secret" not in str(events[-1].payload)
    assert "13800138000" not in str(events[-1].payload)
    failure = await session.scalar(
        select(AgentFailureRecord).where(AgentFailureRecord.run_id == run.id)
    )
    assert failure is not None
    assert failure.code == "agent_model_retries_exhausted"
    assert failure.gateway_request_id is None
    assert "13800138000" not in failure.safe_message
    assert "secret" not in failure.safe_message
    lock = await session.scalar(
        select(SchoolTaskLockRecord).where(SchoolTaskLockRecord.active.is_(True))
    )
    assert lock is not None
    assert lock.owner_run_id == run.id


@pytest.mark.asyncio
async def test_termination_persists_terminal_summary_before_releasing_school_lock(session) -> None:
    task = await create_agent_task(session, tenant_id="school-1", key="supervisor-terminate")
    service = supervisor(session)
    run = await service.start(task_id=task.id, conversation_id=None)

    terminated = await service.terminate(run_id=run.id, reason="operator_requested")

    assert terminated.status == AgentRunStatus.TERMINATED.value
    lock = await session.scalar(
        select(SchoolTaskLockRecord).where(SchoolTaskLockRecord.owner_run_id == run.id)
    )
    assert lock is not None and lock.active is False
    events = await AgentRuntimeRepository(session).list_events(run.id)
    assert events[-2].event_type == "termination.report.persisted"
    assert events[-1].event_type == "run.terminated"


def test_termination_summary_preserves_only_verified_mutation_facts() -> None:
    class Operation:
        id = "op-1"
        status = "succeeded"
        operation_type = "update"
        entity_kind = "student"
        target_source_identifier = "csv:2"
        before = {"name": "旧姓名"}
        actual_after = {"name": "新姓名"}
        verification = {"valid": True}

    assert _termination_mutations((Operation(),)) == [
        {
            "id": "op-1",
            "status": "succeeded",
            "operation": "update",
            "entity_kind": "student",
            "target_source_identifier": "csv:2",
            "before": {"name": "旧姓名"},
            "after": {"name": "新姓名"},
            "verification": {"valid": True},
        }
    ]


@pytest.mark.asyncio
async def test_supervisor_rejects_cross_tenant_or_inactive_conversation(session) -> None:
    task = await create_agent_task(session, tenant_id="school-1", key="supervisor-tenant")
    repository = AgentRuntimeRepository(session)
    foreign = await repository.create_conversation(tenant_id="school-2", created_by="operator-2")

    with pytest.raises(LookupError, match="conversation"):
        await supervisor(session).start(
            task_id=task.id,
            conversation_id=foreign.id,
        )

    local = await repository.create_conversation(tenant_id="school-1", created_by="operator-1")
    local.status = "closed"
    await session.flush()
    with pytest.raises(LookupError, match="conversation"):
        await supervisor(session).start(
            task_id=task.id,
            conversation_id=local.id,
        )


@pytest.mark.asyncio
async def test_supervisor_rejects_legacy_task(session) -> None:
    task = ReconciliationTask(
        tenant_id="school-1",
        scope_id="all",
        snapshot_mode="full",
        entity_types=["student"],
        workflow_version="legacy-v1",
        idempotency_key="legacy-supervisor",
        request_hash="legacy-supervisor",
    )
    session.add(task)
    await session.flush()

    with pytest.raises(ValueError, match="legacy-v1"):
        await supervisor(session).start(
            task_id=task.id,
            conversation_id=None,
        )
