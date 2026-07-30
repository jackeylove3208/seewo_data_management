import json
from asyncio import sleep
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.api_connectors.contracts import (
    ApiProviderError,
    CapturedApiPage,
    ConnectionTestResult,
    FrozenApiRecord,
    ProviderManifest,
)
from app.schemas.agent_ingestion import AgentEntityKind


async def request_json(
    client: httpx.AsyncClient,
    manifest: ProviderManifest,
    method: str,
    url: str,
    *,
    bad_request_code: str = "connector_provider_rejected",
    sleeper: Callable[[float], Awaitable[None]] = sleep,
    **kwargs: Any,
) -> dict[str, Any]:
    host = (urlsplit(url).hostname or "").casefold()
    if host not in manifest.endpoint_hosts:
        raise ApiProviderError("connector_endpoint_policy_violation")
    response: httpx.Response | None = None
    for attempt in range(3):
        try:
            response = await client.request(method, url, **kwargs)
        except (httpx.TimeoutException, httpx.RequestError) as error:
            if attempt < 2:
                await sleeper(0.25 * (2**attempt))
                continue
            safe_code = (
                "connector_timeout"
                if isinstance(error, httpx.TimeoutException)
                else "connector_unavailable"
            )
            raise ApiProviderError(safe_code) from error
        if response.history:
            raise ApiProviderError("connector_endpoint_policy_violation")
        if response.status_code != 429 and response.status_code < 500:
            break
        if attempt < 2:
            await sleeper(_retry_delay(response, attempt))
            continue
        break
    assert response is not None
    if response.status_code in {401}:
        raise ApiProviderError("connector_authentication_failed")
    if response.status_code in {403}:
        raise ApiProviderError("connector_permission_denied")
    if response.status_code == 429:
        raise ApiProviderError("connector_rate_limited")
    if response.status_code >= 500:
        raise ApiProviderError("connector_unavailable")
    if response.status_code == 400:
        raise ApiProviderError(bad_request_code)
    if response.status_code >= 400:
        raise ApiProviderError("connector_provider_rejected")
    try:
        payload = response.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ApiProviderError("connector_invalid_response") from error
    if not isinstance(payload, dict):
        raise ApiProviderError("connector_invalid_response")
    return payload


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after is not None:
        try:
            parsed_delay: float = float(str(retry_after))
            return min(max(parsed_delay, 0.0), 30.0)
        except ValueError:
            pass
    return 0.25 * (2.0**attempt)


def configured_person_kinds(
    public_configuration: Mapping[str, object],
) -> frozenset[AgentEntityKind]:
    values: set[AgentEntityKind] = set()
    default_kind = public_configuration.get("person_entity_kind", "teacher")
    try:
        values.add(AgentEntityKind(str(default_kind)))
    except ValueError as error:
        raise ApiProviderError("connector_configuration_invalid") from error
    configured_rules = public_configuration.get("department_entity_kinds", {})
    if not isinstance(configured_rules, dict):
        raise ApiProviderError("connector_configuration_invalid")
    try:
        values.update(AgentEntityKind(str(item)) for item in configured_rules.values())
    except ValueError as error:
        raise ApiProviderError("connector_configuration_invalid") from error
    if AgentEntityKind.DEPARTMENT in values:
        raise ApiProviderError("connector_configuration_invalid")
    return frozenset(values)


def person_kind(
    public_configuration: Mapping[str, object],
    department_ids: tuple[str, ...],
) -> AgentEntityKind:
    configured_rules = public_configuration.get("department_entity_kinds", {})
    if not isinstance(configured_rules, dict):
        raise ApiProviderError("connector_configuration_invalid")
    matched: set[AgentEntityKind] = set()
    for department_id in department_ids:
        configured = configured_rules.get(department_id)
        if configured is None:
            configured = configured_rules.get(str(department_id))
        if configured is not None:
            try:
                matched.add(AgentEntityKind(str(configured)))
            except ValueError as error:
                raise ApiProviderError("connector_configuration_invalid") from error
    if len(matched) > 1:
        raise ApiProviderError("connector_entity_classification_ambiguous")
    if matched:
        return matched.pop()
    default_kind = public_configuration.get("person_entity_kind", "teacher")
    try:
        kind = AgentEntityKind(str(default_kind))
    except ValueError as error:
        raise ApiProviderError("connector_configuration_invalid") from error
    if kind is AgentEntityKind.DEPARTMENT:
        raise ApiProviderError("connector_configuration_invalid")
    return kind


def require_supported_selection(
    public_configuration: Mapping[str, object],
    selected_entities: frozenset[AgentEntityKind],
) -> None:
    supported = {
        AgentEntityKind.DEPARTMENT,
        *configured_person_kinds(public_configuration),
    }
    if not selected_entities or not selected_entities <= supported:
        raise ApiProviderError("connector_entity_unsupported")


def append_unique_record(
    records: list[FrozenApiRecord],
    unique_records: dict[tuple[AgentEntityKind, str], FrozenApiRecord],
    record: FrozenApiRecord,
) -> None:
    key = (record.entity_kind, record.external_id)
    existing = unique_records.get(key)
    if existing is None:
        unique_records[key] = record
        records.append(record)
    elif existing != record:
        raise ApiProviderError("connector_duplicate_external_id")


def configured_field(
    public_configuration: Mapping[str, object],
    name: str,
    *,
    default: str | None,
) -> str | None:
    value = public_configuration.get(name, default)
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 128
        or not value.replace("_", "").isalnum()
    ):
        raise ApiProviderError("connector_configuration_invalid")
    return value


def department_memberships(value: object, *, fallback: int) -> tuple[int, ...]:
    if value is None:
        return (fallback,)
    if not isinstance(value, list):
        raise ApiProviderError("connector_invalid_response")
    return tuple(positive_int(item) for item in value) or (fallback,)


def record_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ApiProviderError("connector_invalid_response")
    return dict(value)


def required_string(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ApiProviderError("connector_invalid_response")
    return value


def positive_int(value: object) -> int:
    result = non_negative_int(value)
    if result <= 0:
        raise ApiProviderError("connector_invalid_response")
    return result


def non_negative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ApiProviderError("connector_invalid_response")
    try:
        result = int(value)
    except ValueError as error:
        raise ApiProviderError("connector_invalid_response") from error
    if result < 0:
        raise ApiProviderError("connector_invalid_response")
    return result


def capability_summary(
    person_kinds: frozenset[AgentEntityKind],
) -> dict[str, bool]:
    return {
        "organization.read": True,
        "entity.department.read": True,
        "entity.student.read": AgentEntityKind.STUDENT in person_kinds,
        "entity.teacher.read": AgentEntityKind.TEACHER in person_kinds,
    }


async def summarize_connection_test(
    pages: AsyncIterator[CapturedApiPage],
    *,
    person_kinds: frozenset[AgentEntityKind],
) -> ConnectionTestResult:
    counts = {entity.value: 0 for entity in AgentEntityKind}
    async for page in pages:
        for record in page.records:
            counts[record.entity_kind.value] += 1
    record_count = sum(counts.values())
    visible = record_count > 0
    return ConnectionTestResult(
        eligible=visible,
        capabilities=capability_summary(person_kinds),
        visibility_summary={
            "visible": visible,
            "record_count": record_count,
            **{f"{kind}_count": count for kind, count in counts.items()},
        },
        safe_error_code=None if visible else "connector_visibility_empty",
    )


def projected_fields(
    raw: Mapping[str, object],
    *,
    entity_kind: AgentEntityKind,
    number_field: str | None,
    class_name_field: str | None,
) -> tuple[dict[str, str | None], tuple[str, ...]]:
    unavailable: list[str] = []

    def field(source_field: str | None, contract_field: str) -> str | None:
        if source_field is None or source_field not in raw:
            unavailable.append(contract_field)
            return None
        value = raw[source_field]
        if value is None:
            return None
        if not isinstance(value, (str, int)):
            raise ApiProviderError("connector_invalid_response")
        return str(value)

    return (
        {
            "category": {
                AgentEntityKind.DEPARTMENT: "部门",
                AgentEntityKind.STUDENT: "学生",
                AgentEntityKind.TEACHER: "教师",
            }[entity_kind],
            "name": field("name", "name"),
            "number": field(number_field, "number"),
            "class_name": (
                field(class_name_field, "class_name")
                if entity_kind is AgentEntityKind.STUDENT
                else None
            ),
            "phone": field("mobile", "phone"),
            "email": field("email", "email"),
        },
        tuple(unavailable),
    )
