import asyncio

import pytest

from app.ai.providers.base import TransientModelError
from app.ai.rematching_worker import EntityRematchingWorker
from app.repositories.rematching import EntityRematchRepository
from app.schemas.rematching import ManualReviewDecision, NoMatchDecision
from tests.integration.repositories.test_entity_rematch_jobs import create_job


class SuccessfulAgent:
    def __init__(self) -> None:
        self.calls = 0

    async def decide(self, **kwargs):
        self.calls += 1
        assert kwargs["candidate_edges"]
        return NoMatchDecision(confidence=0.92, reason="所有候选的联系方式均不一致")


class TransientAgent:
    async def decide(self, **kwargs):
        raise TransientModelError("gateway timeout")


@pytest.mark.asyncio
async def test_worker_commits_claim_before_agent_call_and_completes(database, session) -> None:
    job = await create_job(session)
    await session.commit()
    agent = SuccessfulAgent()
    worker = EntityRematchingWorker(
        database.session_factory,
        agent=agent,
        worker_id="rematch-worker-1",
        lease_seconds=60,
        retry_wait_seconds=0,
    )

    assert await worker.run_once() is True
    async with database.session_factory() as check_session:
        refreshed = await EntityRematchRepository(check_session).get_for_tenant(job.id, "school-1")
        items = await EntityRematchRepository(check_session).work_items(job.id, "school-1")
        assert refreshed is not None
        assert refreshed.status == "completed"
        assert refreshed.no_match == 1
        assert items[0].status == "no_match"
        assert agent.calls == 1


@pytest.mark.asyncio
async def test_worker_schedules_transient_failure_with_backoff(database, session) -> None:
    job = await create_job(session)
    await session.commit()
    worker = EntityRematchingWorker(
        database.session_factory,
        agent=TransientAgent(),
        worker_id="rematch-worker-retry",
        lease_seconds=60,
        retry_wait_seconds=30,
    )

    assert await worker.run_once() is True
    async with database.session_factory() as check_session:
        items = await EntityRematchRepository(check_session).work_items(job.id, "school-1")
        assert items[0].status == "retry_wait"
        assert items[0].failure_code == "model_provider_transient"


@pytest.mark.asyncio
async def test_worker_exhaustion_persists_chinese_manual_fallback(database, session) -> None:
    job = await create_job(session)
    repository = EntityRematchRepository(session)
    item = (await repository.work_items(job.id, "school-1"))[0]
    item.max_attempts = 1
    await session.commit()
    worker = EntityRematchingWorker(
        database.session_factory,
        agent=TransientAgent(),
        worker_id="rematch-worker-fallback",
        lease_seconds=60,
        retry_wait_seconds=0,
    )

    assert await worker.run_once() is True
    async with database.session_factory() as check_session:
        items = await EntityRematchRepository(check_session).work_items(job.id, "school-1")
        refreshed = await EntityRematchRepository(check_session).get_for_tenant(job.id, "school-1")
        assert refreshed is not None
        assert refreshed.manual_review == 1
        assert isinstance(
            ManualReviewDecision.model_validate(items[0].outcome), ManualReviewDecision
        )


@pytest.mark.asyncio
async def test_worker_heartbeats_until_terminal_commit(database, session, monkeypatch) -> None:
    await create_job(session)
    await session.commit()
    original_complete = EntityRematchRepository.complete_item
    heartbeat_calls = 0
    original_heartbeat = EntityRematchRepository.heartbeat

    async def delayed_complete(self, *args, **kwargs):
        await asyncio.sleep(0.5)
        return await original_complete(self, *args, **kwargs)

    async def record_heartbeat(self, *args, **kwargs):
        nonlocal heartbeat_calls
        heartbeat_calls += 1
        return await original_heartbeat(self, *args, **kwargs)

    monkeypatch.setattr(EntityRematchRepository, "complete_item", delayed_complete)
    monkeypatch.setattr(EntityRematchRepository, "heartbeat", record_heartbeat)
    worker = EntityRematchingWorker(
        database.session_factory,
        agent=SuccessfulAgent(),
        worker_id="rematch-worker-heartbeat",
        lease_seconds=1,
    )

    assert await worker.run_once() is True
    assert heartbeat_calls >= 1
