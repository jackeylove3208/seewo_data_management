from collections.abc import Awaitable, Callable

import httpx

from app.api_connectors.dingtalk import DINGTALK_MANIFEST
from app.api_connectors.provider_runtime import request_json


async def test_request_json_retries_rate_limits_before_succeeding() -> None:
    attempts = 0
    delays: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(429, headers={"Retry-After": "0.1"})
        return httpx.Response(200, json={"ok": True})

    async def sleep(delay: float) -> None:
        delays.append(delay)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        payload = await request_json(
            client,
            DINGTALK_MANIFEST,
            "GET",
            "https://api.dingtalk.com/v1.0/test",
            sleeper=sleep,
        )

    assert payload == {"ok": True}
    assert attempts == 3
    assert delays == [0.1, 0.1]


async def test_request_json_retries_transient_transport_failure() -> None:
    attempts = 0
    delays: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("temporary failure", request=request)
        return httpx.Response(200, json={"ok": True})

    async def sleep(delay: float) -> None:
        delays.append(delay)

    sleeper: Callable[[float], Awaitable[None]] = sleep
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        payload = await request_json(
            client,
            DINGTALK_MANIFEST,
            "GET",
            "https://api.dingtalk.com/v1.0/test",
            sleeper=sleeper,
        )

    assert payload == {"ok": True}
    assert attempts == 2
    assert delays == [0.25]
