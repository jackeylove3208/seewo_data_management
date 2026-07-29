import json
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from app.api_connectors.contracts import (
    AgentProjectionContext,
    ApiProviderError,
    FrozenApiRecord,
    ProviderManifest,
)
from app.schemas.agent_ingestion import (
    AgentContractRecord,
    AgentEntityKind,
    AgentSourceRole,
)


async def request_json(
    client: httpx.AsyncClient,
    manifest: ProviderManifest,
    method: str,
    url: str,
    *,
    bad_request_code: str = "connector_provider_rejected",
    **kwargs: Any,
) -> dict[str, Any]:
    host = (urlsplit(url).hostname or "").casefold()
    if host not in manifest.endpoint_hosts:
        raise ApiProviderError("connector_endpoint_policy_violation")
    try:
        response = await client.request(method, url, **kwargs)
    except httpx.TimeoutException as error:
        raise ApiProviderError("connector_timeout") from error
    except httpx.RequestError as error:
        raise ApiProviderError("connector_unavailable") from error
    if response.history:
        raise ApiProviderError("connector_endpoint_policy_violation")
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


def project_record(
    record: FrozenApiRecord,
    context: AgentProjectionContext,
) -> AgentContractRecord:
    fields = record.projected_fields
    return AgentContractRecord(
        task_id=context.task_id,
        run_id=context.run_id,
        snapshot_id=context.snapshot_id,
        tenant_id=context.tenant_id,
        source_role=AgentSourceRole.AUTHORITATIVE,
        stable_locator=(
            f"api:{context.connection_id}:{record.entity_kind}:"
            f"{quote(record.external_id, safe='')}"
        ),
        stable_order=context.stable_order,
        entity_kind=record.entity_kind,
        category=fields.get("category"),
        name=fields.get("name"),
        number=fields.get("number"),
        class_name=(
            fields.get("class_name")
            if record.entity_kind is AgentEntityKind.STUDENT
            else None
        ),
        phone=fields.get("phone"),
        email=fields.get("email"),
        raw_row_number=None,
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
