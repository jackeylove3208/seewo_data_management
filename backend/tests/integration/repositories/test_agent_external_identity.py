from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.agent_runtime.external_identity_service import (
    AgentExternalIdentityService,
    ExternalIdentityBindingConflict,
    ExternalIdentityBindingValidation,
)
from app.models.agent_analysis import (
    AgentIdentityClaimRecord,
    AgentIdentityPostingRecord,
    AgentInputMarkRecord,
    AgentWorkItemRecord,
)
from app.models.agent_runtime import AgentRunRecord
from app.models.api_connectors import ApiConnectionRecord
from app.models.reconciliation import ReconciliationTask
from app.models.snapshots import Snapshot, SourceFile
from app.reconciliation.agent_identity import AgentIdentityIndexBuilder
from app.repositories.agent_analysis import AgentAnalysisRepository
from app.schemas.agent_ingestion import (
    AgentContractRecord,
    AgentEntityKind,
    AgentSourceRole,
)


async def _seed_context(
    session,
    *,
    tenant_id: str = "school-1",
    connection_id: UUID | None = None,
    authority_number: str | None = None,
    target_rows: tuple[tuple[str, str], ...] = (("target-1", "T-001"),),
):
    connection_id = connection_id or uuid4()
    connection = await session.get(ApiConnectionRecord, connection_id)
    if connection is None:
        connection = ApiConnectionRecord(
            id=connection_id,
            tenant_id=tenant_id,
            provider_id="dingtalk",
            display_name=f"钉钉-{tenant_id}",
            public_configuration={"person_entity_kind": "teacher"},
            secret_ref=f"db-secret:{uuid4()}",
            manifest_version="1.0.0",
            adapter_version="1.0.0",
            capabilities={"entity.teacher.read": True},
            visibility_summary={"visible": True, "teacher_count": 1},
            state="active",
            created_by="operator-1",
            updated_by="operator-1",
        )
        session.add(connection)
        await session.flush()
    task = ReconciliationTask(
        tenant_id=tenant_id,
        scope_id="all",
        snapshot_mode="full",
        entity_types=["teacher"],
        status="running",
        stage="ingestion",
        workflow_version="agent-graph-v1",
        task_kind="sync",
        title="外部身份测试",
        agent_intent={
            "source": {
                "kind": "api",
                "configuration_id": str(connection_id),
            },
            "target": {
                "kind": "database",
                "configuration_id": "seewo-mysql",
            },
        },
        idempotency_key=str(uuid4()),
        request_hash=uuid4().hex * 2,
    )
    session.add(task)
    await session.flush()
    run = AgentRunRecord(
        task_id=task.id,
        tenant_id=tenant_id,
        kind="sync",
        workflow_version="agent-graph-v1",
        ingestion_contract_version="source-ingestion-v3",
        execution_contract_version="deterministic-execution-v2",
        phase="build_identity_index",
        status="pending",
    )
    session.add(run)
    await session.flush()
    snapshots: dict[str, Snapshot] = {}
    for role in ("authoritative", "target"):
        source = SourceFile(
            task_id=task.id,
            source_role=role,
            original_name=f"{role}.jsonl",
            storage_name=f"{role}-{uuid4().hex}",
            storage_path=f"memory://{role}",
            managed_storage=False,
            sha256=("a" if role == "authoritative" else "b") * 64,
            size_bytes=1,
            detected_encoding="utf-8",
        )
        session.add(source)
        await session.flush()
        snapshot = Snapshot(
            id=uuid4(),
            task_id=task.id,
            source_file_id=source.id,
            source_role=role,
            schema_version="source-ingestion-v3",
            mapping_version="test-v1",
            file_hash=source.sha256,
            content_hash=source.sha256,
            state="published",
            summary={},
        )
        session.add(snapshot)
        snapshots[role] = snapshot
    await session.flush()
    authority_locator = f"api:{connection_id}:teacher:ding-user-42"
    records = [
        AgentContractRecord(
            task_id=task.id,
            run_id=run.id,
            snapshot_id=snapshots["authoritative"].id,
            tenant_id=tenant_id,
            source_role=AgentSourceRole.AUTHORITATIVE,
            stable_locator=authority_locator,
            stable_order=1,
            entity_kind=AgentEntityKind.TEACHER,
            category="教师",
            name="周明远",
            number=authority_number,
        )
    ]
    records.extend(
        AgentContractRecord(
            task_id=task.id,
            run_id=run.id,
            snapshot_id=snapshots["target"].id,
            tenant_id=tenant_id,
            source_role=AgentSourceRole.TARGET,
            stable_locator=f"database:seewo-mysql:{locator}",
            stable_order=index,
            entity_kind=AgentEntityKind.TEACHER,
            category="教师",
            name="周明远",
            number=number,
        )
        for index, (locator, number) in enumerate(target_rows, start=1)
    )
    inputs = await AgentAnalysisRepository(session).persist_inputs(tuple(records))
    return task, run, connection, inputs[0], inputs[1:]


async def test_valid_external_binding_claims_authority_without_ordinary_keys(
    session,
) -> None:
    _task, run, connection, authority, targets = await _seed_context(session)
    target = targets[0]
    binding = await AgentExternalIdentityService(session).confirm(
        tenant_id=run.tenant_id,
        operator_id="operator-1",
        run_id=run.id,
        connection_id=connection.id,
        entity_kind="teacher",
        authority_stable_locator=authority.stable_locator,
        target_connector_id="seewo-mysql",
        target_stable_locator=target.stable_locator,
    )

    await AgentIdentityIndexBuilder(session).build(run_id=run.id)

    claim = await session.scalar(select(AgentIdentityClaimRecord))
    postings = tuple(await session.scalars(select(AgentIdentityPostingRecord)))
    assert binding.status == "active"
    assert binding.binding_version == 1
    assert claim is not None
    assert claim.authority_input_id == authority.id
    assert claim.target_input_id == target.id
    assert all(item.input_record_id != authority.id for item in postings)
    assert {item.key_kind for item in postings} <= {"number", "phone", "email"}


async def test_authority_without_key_or_binding_is_invalid_not_target_missing(
    session,
) -> None:
    _task, run, _connection, authority, _targets = await _seed_context(session)

    await AgentIdentityIndexBuilder(session).build(run_id=run.id)

    work = tuple(
        await session.scalars(
            select(AgentWorkItemRecord).where(
                AgentWorkItemRecord.subject_input_id == authority.id
            )
        )
    )
    mark = await session.scalar(
        select(AgentInputMarkRecord).where(
            AgentInputMarkRecord.input_record_id == authority.id,
            AgentInputMarkRecord.reason_code == "authority_identity_absent",
        )
    )
    assert [item.kind for item in work] == ["authority_invalid"]
    assert mark is not None
    assert mark.inclusion_state == "excluded"


async def test_legacy_authority_without_key_keeps_existing_target_missing_behavior(
    session,
) -> None:
    _task, run, _connection, authority, _targets = await _seed_context(session)
    run.ingestion_contract_version = "source-ingestion-v2"
    await session.flush()

    await AgentIdentityIndexBuilder(session).build(run_id=run.id)

    work = tuple(
        await session.scalars(
            select(AgentWorkItemRecord).where(
                AgentWorkItemRecord.subject_input_id == authority.id
            )
        )
    )
    absent_mark = await session.scalar(
        select(AgentInputMarkRecord).where(
            AgentInputMarkRecord.input_record_id == authority.id,
            AgentInputMarkRecord.reason_code == "authority_identity_absent",
        )
    )
    assert [item.kind for item in work] == ["target_missing"]
    assert absent_mark is None


async def test_binding_rejects_competing_active_target(session) -> None:
    _task, run, connection, authority, targets = await _seed_context(
        session,
        target_rows=(("target-1", "T-001"), ("target-2", "T-002")),
    )
    service = AgentExternalIdentityService(session)
    await service.confirm(
        tenant_id=run.tenant_id,
        operator_id="operator-1",
        run_id=run.id,
        connection_id=connection.id,
        entity_kind="teacher",
        authority_stable_locator=authority.stable_locator,
        target_connector_id="seewo-mysql",
        target_stable_locator=targets[0].stable_locator,
    )

    with pytest.raises(ExternalIdentityBindingConflict):
        await service.confirm(
            tenant_id=run.tenant_id,
            operator_id="operator-1",
            run_id=run.id,
            connection_id=connection.id,
            entity_kind="teacher",
            authority_stable_locator=authority.stable_locator,
            target_connector_id="seewo-mysql",
            target_stable_locator=targets[1].stable_locator,
        )


async def test_external_binding_and_ordinary_identity_disagreement_is_conflict(
    session,
) -> None:
    _task, run, connection, authority, targets = await _seed_context(
        session,
        authority_number="T-002",
        target_rows=(("bound-target", "T-001"), ("ordinary-target", "T-002")),
    )
    await AgentExternalIdentityService(session).confirm(
        tenant_id=run.tenant_id,
        operator_id="operator-1",
        run_id=run.id,
        connection_id=connection.id,
        entity_kind="teacher",
        authority_stable_locator=authority.stable_locator,
        target_connector_id="seewo-mysql",
        target_stable_locator=targets[0].stable_locator,
    )

    await AgentIdentityIndexBuilder(session).build(run_id=run.id)

    claims = tuple(await session.scalars(select(AgentIdentityClaimRecord)))
    works = tuple(
        await session.scalars(
            select(AgentWorkItemRecord).where(
                AgentWorkItemRecord.run_id == run.id
            )
        )
    )
    assert claims == ()
    assert [item.kind for item in works] == ["identity_conflict"]


async def test_stale_binding_target_creates_safe_authority_exception(session) -> None:
    _task, first_run, connection, authority, targets = await _seed_context(session)
    service = AgentExternalIdentityService(session)
    await service.confirm(
        tenant_id=first_run.tenant_id,
        operator_id="operator-1",
        run_id=first_run.id,
        connection_id=connection.id,
        entity_kind="teacher",
        authority_stable_locator=authority.stable_locator,
        target_connector_id="seewo-mysql",
        target_stable_locator=targets[0].stable_locator,
    )
    _task, second_run, _connection, second_authority, _targets = (
        await _seed_context(
            session,
            connection_id=connection.id,
            target_rows=(("replacement-target", "T-002"),),
        )
    )

    await AgentIdentityIndexBuilder(session).build(run_id=second_run.id)

    stale_mark = await session.scalar(
        select(AgentInputMarkRecord).where(
            AgentInputMarkRecord.input_record_id == second_authority.id,
            AgentInputMarkRecord.reason_code
            == "external_identity_binding_stale",
        )
    )
    authority_work = await session.scalar(
        select(AgentWorkItemRecord).where(
            AgentWorkItemRecord.run_id == second_run.id,
            AgentWorkItemRecord.subject_input_id == second_authority.id,
        )
    )
    claim = await session.scalar(
        select(AgentIdentityClaimRecord).where(
            AgentIdentityClaimRecord.run_id == second_run.id
        )
    )
    assert stale_mark is not None
    assert authority_work is not None and authority_work.kind == "authority_invalid"
    assert claim is None


async def test_revoked_binding_is_not_applied_to_later_run(session) -> None:
    _task, first_run, connection, authority, targets = await _seed_context(session)
    service = AgentExternalIdentityService(session)
    binding = await service.confirm(
        tenant_id=first_run.tenant_id,
        operator_id="operator-1",
        run_id=first_run.id,
        connection_id=connection.id,
        entity_kind="teacher",
        authority_stable_locator=authority.stable_locator,
        target_connector_id="seewo-mysql",
        target_stable_locator=targets[0].stable_locator,
    )
    revoked = await service.revoke(
        tenant_id=first_run.tenant_id,
        operator_id="operator-2",
        binding_id=binding.id,
    )
    _task, later_run, _connection, later_authority, _targets = (
        await _seed_context(
            session,
            connection_id=connection.id,
        )
    )

    await AgentIdentityIndexBuilder(session).build(run_id=later_run.id)

    claim = await session.scalar(
        select(AgentIdentityClaimRecord).where(
            AgentIdentityClaimRecord.run_id == later_run.id
        )
    )
    absent = await session.scalar(
        select(AgentInputMarkRecord).where(
            AgentInputMarkRecord.input_record_id == later_authority.id,
            AgentInputMarkRecord.reason_code == "authority_identity_absent",
        )
    )
    assert revoked.status == "revoked"
    assert revoked.revoked_by == "operator-2"
    assert revoked.revoked_at is not None
    assert claim is None
    assert absent is not None


async def test_binding_confirmation_is_tenant_scoped(session) -> None:
    _task, run, connection, authority, targets = await _seed_context(session)

    with pytest.raises(ExternalIdentityBindingValidation):
        await AgentExternalIdentityService(session).confirm(
            tenant_id="school-2",
            operator_id="operator-2",
            run_id=run.id,
            connection_id=connection.id,
            entity_kind="teacher",
            authority_stable_locator=authority.stable_locator,
            target_connector_id="seewo-mysql",
            target_stable_locator=targets[0].stable_locator,
        )
