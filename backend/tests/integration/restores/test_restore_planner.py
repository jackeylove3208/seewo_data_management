import pytest

from app.repositories.executions import ExecutionRepository
from app.restores.planner import HistoricalRestorePlanner
from app.schemas.executions import OperationType
from tests.integration.repositories.test_reporting import _execution


@pytest.mark.asyncio
async def test_backward_restore_inverts_verified_operation(session) -> None:
    pair, batch, root, output = await _execution(session)
    executions = ExecutionRepository(session)
    stored = (await executions.list_operations(batch.id))[0]
    await executions.append_attempt(
        stored.id,
        status="succeeded",
        actual_after=stored.after,
        verification={"valid": True},
        target_version_id=output.id,
    )

    result = await HistoricalRestorePlanner(session).plan(
        source_version_id=output.id,
        target_version_id=root.id,
        tenant_id=pair.tenant_id,
    )

    assert result.allowed is True
    assert result.operations[0].operation_type is OperationType.UPDATE
    assert result.operations[0].before == {"phone": "200"}
    assert result.operations[0].after == {"phone": "100"}
    assert result.operations[0].compensation_for == stored.operation_id
    repeated = await HistoricalRestorePlanner(session).plan(
        source_version_id=output.id,
        target_version_id=root.id,
        tenant_id=pair.tenant_id,
    )
    assert repeated.operations[0].id == result.operations[0].id


@pytest.mark.asyncio
async def test_forward_restore_replays_verified_operation(session) -> None:
    pair, batch, root, output = await _execution(session)
    executions = ExecutionRepository(session)
    stored = (await executions.list_operations(batch.id))[0]
    await executions.append_attempt(
        stored.id,
        status="succeeded",
        actual_after=stored.after,
        verification={"valid": True},
        target_version_id=output.id,
    )

    result = await HistoricalRestorePlanner(session).plan(
        source_version_id=root.id,
        target_version_id=output.id,
        tenant_id=pair.tenant_id,
    )

    assert result.allowed is True
    assert result.operations[0].before == {"phone": "100"}
    assert result.operations[0].after == {"phone": "200"}


@pytest.mark.asyncio
async def test_uncertain_operation_blocks_restore(session) -> None:
    pair, batch, root, output = await _execution(session)
    executions = ExecutionRepository(session)
    stored = (await executions.list_operations(batch.id))[0]
    await executions.append_attempt(
        stored.id,
        status="verification_failed",
        actual_after=stored.after,
        verification={"valid": False},
        target_version_id=output.id,
    )

    result = await HistoricalRestorePlanner(session).plan(
        source_version_id=output.id,
        target_version_id=root.id,
        tenant_id=pair.tenant_id,
    )

    assert result.allowed is False
    assert result.conflicts[0].code == "uncertain_operation"


@pytest.mark.asyncio
async def test_succeeded_attempt_for_another_version_blocks_restore(session) -> None:
    pair, batch, root, output = await _execution(session)
    executions = ExecutionRepository(session)
    stored = (await executions.list_operations(batch.id))[0]
    await executions.append_attempt(
        stored.id,
        status="succeeded",
        actual_after=stored.after,
        verification={"valid": True},
        target_version_id=root.id,
    )

    result = await HistoricalRestorePlanner(session).plan(
        source_version_id=output.id,
        target_version_id=root.id,
        tenant_id=pair.tenant_id,
    )

    assert result.allowed is False
    assert result.conflicts[0].code == "uncertain_operation"
