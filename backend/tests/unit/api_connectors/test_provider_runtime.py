from collections.abc import Awaitable, Callable

import httpx
import pytest

from app.api_connectors.contracts import ApiProviderError
from app.api_connectors.dingtalk import DINGTALK_MANIFEST
from app.api_connectors.provider_runtime import (
    configured_person_kinds,
    person_kind,
    request_json,
    require_supported_selection,
)
from app.schemas.agent_ingestion import AgentEntityKind


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


def test_new_people_scope_requires_server_classification_map() -> None:
    configuration = {
        "sync_scope": "people",
        "person_classification_mode": "organization_unit_llm",
    }

    with pytest.raises(ApiProviderError) as captured:
        person_kind(configuration, ("10",))

    assert captured.value.safe_code == "connector_configuration_invalid"


def test_new_people_scope_uses_map_and_supports_both_person_kinds() -> None:
    configuration = {
        "sync_scope": "people",
        "person_classification_mode": "organization_unit_llm",
        "department_entity_kinds": {"10": "teacher", "20": "student"},
    }

    assert configured_person_kinds(configuration) == frozenset(
        {AgentEntityKind.TEACHER, AgentEntityKind.STUDENT}
    )
    assert person_kind(configuration, ("10",)) is AgentEntityKind.TEACHER
    assert person_kind(configuration, ("20",)) is AgentEntityKind.STUDENT
    require_supported_selection(
        configuration,
        frozenset({AgentEntityKind.TEACHER, AgentEntityKind.STUDENT}),
    )


def test_membership_decision_overrides_neutral_or_conflicting_departments() -> None:
    configuration = {
        "sync_scope": "people",
        "person_classification_mode": "organization_unit_llm",
        "department_entity_kinds": {"10": "teacher", "20": "student"},
        "person_membership_entity_kinds": {
            "1|20": "student",
            "10|20": "student",
        },
    }

    assert person_kind(configuration, ("20", "1")) is AgentEntityKind.STUDENT
    assert person_kind(configuration, ("20", "10")) is AgentEntityKind.STUDENT


def test_department_scope_rejects_person_selection() -> None:
    configuration = {"sync_scope": "department"}

    require_supported_selection(
        configuration,
        frozenset({AgentEntityKind.DEPARTMENT}),
    )
    with pytest.raises(ApiProviderError) as captured:
        require_supported_selection(
            configuration,
            frozenset({AgentEntityKind.TEACHER}),
        )

    assert captured.value.safe_code == "connector_entity_unsupported"
