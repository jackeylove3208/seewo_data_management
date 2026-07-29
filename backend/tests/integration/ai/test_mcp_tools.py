from uuid import uuid4

import pytest

from app.ai.mcp.authorization import ToolAuthorizationError, ToolContext
from app.ai.mcp.server import MCPToolGateway
from app.differences.service import DifferenceDetectionService
from app.matching.service import EntityResolutionService
from app.models.reconciliation import ReconciliationTask
from app.repositories.differences import DifferenceRepository
from app.repositories.rematching import EntityRematchRepository
from app.schemas.differences import DifferenceType
from tests.fixtures.organization_factory import create_hierarchy_pair
from tests.integration.ai.test_analysis_service import seed_difference
from tests.integration.repositories.test_entity_rematch_jobs import draft


@pytest.fixture
async def difference(session):
    pair = await create_hierarchy_pair(session)
    await EntityResolutionService(session).resolve(pair)
    await DifferenceDetectionService(session).detect(pair.task_id)
    return (await DifferenceRepository(session).for_task(pair.task_id))[0]


def context_for(difference, **overrides) -> ToolContext:
    values = {
        "operator_id": "operator-1",
        "tenant_id": difference.tenant_id,
        "task_id": difference.task_id,
        "allowed_difference_ids": frozenset({difference.id}),
    }
    values.update(overrides)
    return ToolContext.model_validate(values)


@pytest.mark.asyncio
async def test_difference_context_requires_backend_allow_list(session, difference) -> None:
    gateway = MCPToolGateway(session)

    result = await gateway.call(
        "difference_context",
        {"difference_id": str(difference.id)},
        context_for(difference),
    )

    assert result.payload["id"] == str(difference.id)
    assert result.trace_id

    with pytest.raises(ToolAuthorizationError, match="not authorized"):
        await gateway.call(
            "difference_context",
            {"difference_id": str(difference.id)},
            context_for(difference, allowed_difference_ids=frozenset()),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("override", "value"),
    [("tenant_id", "other-school"), ("task_id", uuid4())],
)
async def test_difference_context_rejects_cross_scope(
    session,
    difference,
    override,
    value,
) -> None:
    with pytest.raises(ToolAuthorizationError, match="not authorized"):
        await MCPToolGateway(session).call(
            "difference_context",
            {"difference_id": str(difference.id)},
            context_for(difference, **{override: value}),
        )


@pytest.mark.asyncio
async def test_gateway_has_only_read_tools_and_rejects_unknown_tool(session, difference) -> None:
    gateway = MCPToolGateway(session)
    assert gateway.tool_names == {
        "difference_context",
        "candidate_search",
        "rematch_candidate_evidence",
        "mapping_rules",
        "execution_context",
    }

    with pytest.raises(ToolAuthorizationError, match="tool is not allowed"):
        await gateway.call("apply_target_update", {}, context_for(difference))


@pytest.mark.asyncio
async def test_candidate_search_has_a_bounded_top_k(session, difference) -> None:
    with pytest.raises(ValueError, match="top_k"):
        await MCPToolGateway(session).call(
            "candidate_search",
            {"difference_id": str(difference.id), "query": "teacher", "top_k": 11},
            context_for(difference),
        )


@pytest.mark.asyncio
async def test_candidate_search_queries_target_snapshot_entities(session) -> None:
    difference = await seed_difference(session, DifferenceType.ATTRIBUTE_CONFLICT)

    result = await MCPToolGateway(session).call(
        "candidate_search",
        {
            "difference_id": str(difference.id),
            "query": "张三",
            "top_k": 5,
        },
        context_for(difference),
    )

    assert result.payload["total"] == 1
    assert result.payload["items"][0]["source_id"] == "sw-t1"
    assert result.payload["items"][0]["source_role"] == "target"


@pytest.mark.asyncio
async def test_rematch_candidate_evidence_is_task_scoped_and_read_only(session, difference) -> None:
    task = await session.get(ReconciliationTask, difference.task_id)
    assert task is not None
    item = draft(
        focal_entity_id=difference.evidence.source_entity_id or uuid4(),
        candidate_entity_id=difference.evidence.target_entity_id or uuid4(),
    )
    source_snapshot_id, target_snapshot_id = (
        difference.evidence.source_snapshot_id,
        difference.evidence.target_snapshot_id,
    )
    job = await EntityRematchRepository(session).create_or_get(
        task_id=task.id,
        tenant_id=difference.tenant_id,
        requested_by="operator-1",
        source_snapshot_id=source_snapshot_id,
        target_snapshot_id=target_snapshot_id,
        idempotency_key=uuid4().hex,
        policy_version="rematch-v1",
        items=(item,),
    )
    work_item = (await EntityRematchRepository(session).work_items(job.id, difference.tenant_id))[0]
    result = await MCPToolGateway(session).call(
        "rematch_candidate_evidence",
        {"difference_id": str(difference.id), "work_item_id": str(work_item.id)},
        context_for(difference),
    )
    assert result.payload["job_id"] == str(job.id)
    assert result.payload["candidates"][0]["rank"] == 1
    assert "payload" not in result.payload["candidates"][0]
    with pytest.raises(ToolAuthorizationError, match="not authorized"):
        await MCPToolGateway(session).call(
            "rematch_candidate_evidence",
            {"difference_id": str(difference.id), "work_item_id": str(work_item.id)},
            context_for(difference, tenant_id="other-school"),
        )
