import asyncio
from collections.abc import Awaitable, Callable


class AgentModelRetriesExhausted(RuntimeError):
    code = "agent_model_retries_exhausted"

    def __init__(self, attempt_count: int) -> None:
        self.attempt_count = attempt_count
        super().__init__(f"Agent model processing failed after {attempt_count} attempts")


async def run_model_with_retries[T](
    operation: Callable[[], Awaitable[T]],
    *,
    retry_wait_seconds: float,
    retry_attempts: int = 3,
) -> T:
    total_attempts = retry_attempts + 1
    for attempt in range(1, total_attempts + 1):
        try:
            return await operation()
        except Exception:
            if attempt == total_attempts:
                raise AgentModelRetriesExhausted(total_attempts) from None
            if retry_wait_seconds:
                await asyncio.sleep(retry_wait_seconds)
    raise AssertionError("unreachable")
