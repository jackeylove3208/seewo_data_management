import pytest

from app.agent_runtime.retry import AgentModelRetriesExhausted, run_model_with_retries


@pytest.mark.asyncio
async def test_model_operation_gets_one_initial_attempt_and_three_retries() -> None:
    attempts = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts < 4:
            raise TimeoutError("provider timeout with private detail")
        return "ok"

    result = await run_model_with_retries(operation, retry_wait_seconds=0)

    assert result == "ok"
    assert attempts == 4


@pytest.mark.asyncio
async def test_model_retry_exhaustion_exposes_only_a_stable_safe_error() -> None:
    async def operation() -> str:
        raise RuntimeError("secret prompt and stack detail")

    with pytest.raises(AgentModelRetriesExhausted) as captured:
        await run_model_with_retries(operation, retry_wait_seconds=0)

    assert captured.value.code == "agent_model_retries_exhausted"
    assert captured.value.attempt_count == 4
    assert str(captured.value) == "Agent model processing failed after 4 attempts"
    assert "secret" not in repr(captured.value)
