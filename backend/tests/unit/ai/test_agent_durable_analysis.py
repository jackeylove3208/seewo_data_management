import pytest

from app.ai.agent_durable_analysis import analyze_with_four_total_attempts
from app.ai.providers.base import TransientModelError


@pytest.mark.asyncio
async def test_retries_three_times_after_initial_attempt_then_returns_result() -> None:
    calls = 0

    async def attempt() -> str:
        nonlocal calls
        calls += 1
        if calls < 4:
            raise TransientModelError("temporary")
        return "ok"

    assert await analyze_with_four_total_attempts(attempt) == "ok"
    assert calls == 4


@pytest.mark.asyncio
async def test_stops_after_exactly_four_attempts() -> None:
    calls = 0

    async def attempt() -> str:
        nonlocal calls
        calls += 1
        raise TransientModelError("temporary")

    with pytest.raises(TransientModelError):
        await analyze_with_four_total_attempts(attempt)

    assert calls == 4
