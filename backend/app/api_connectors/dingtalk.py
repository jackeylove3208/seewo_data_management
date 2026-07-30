from collections.abc import AsyncIterator, Mapping
from typing import Any

import httpx

from app.api_connectors.contracts import (
    ApiProviderError,
    CapturedApiPage,
    ConnectionTestResult,
    FrozenApiRecord,
    ProviderManifest,
)
from app.api_connectors.provider_runtime import (
    append_unique_record,
    configured_field,
    configured_person_kinds,
    department_memberships,
    non_negative_int,
    person_kind,
    positive_int,
    projected_fields,
    record_dict,
    request_json,
    require_supported_selection,
    required_string,
    summarize_connection_test,
)
from app.schemas.agent_ingestion import AgentEntityKind

DINGTALK_MANIFEST = ProviderManifest(
    provider_id="dingtalk",
    manifest_version="2026-07-29",
    adapter_version="1.0.0",
    supported_entities=frozenset(AgentEntityKind),
    required_secret_fields=("app_key", "app_secret"),
    required_capabilities=("contact.department.read", "contact.user.read"),
    endpoint_hosts=("api.dingtalk.com", "oapi.dingtalk.com"),
    maximum_pages=10_000,
    projection_version="organization-six-fields-v1",
)

_TOKEN_URL = "https://api.dingtalk.com/v1.0/oauth2/accessToken"
_OAPI_BASE = "https://oapi.dingtalk.com"
_PAGE_SIZE = 100


class DingtalkOrganizationAdapter:
    manifest = DINGTALK_MANIFEST

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def test_connection(
        self,
        public_configuration: Mapping[str, object],
        secret: Mapping[str, str],
    ) -> ConnectionTestResult:
        person_kinds = configured_person_kinds(public_configuration)
        selected = frozenset({AgentEntityKind.DEPARTMENT, *person_kinds})
        return await summarize_connection_test(
            self.capture(public_configuration, secret, selected),
            person_kinds=person_kinds,
        )

    async def capture(
        self,
        public_configuration: Mapping[str, object],
        secret: Mapping[str, str],
        selected_entities: frozenset[AgentEntityKind],
    ) -> AsyncIterator[CapturedApiPage]:
        require_supported_selection(public_configuration, selected_entities)
        token = await self._access_token(secret)
        root_department_id = positive_int(
            public_configuration.get("root_department_id", 1)
        )
        number_field = configured_field(
            public_configuration,
            "number_field",
            default="job_number",
        )
        class_name_field = configured_field(
            public_configuration,
            "class_name_field",
            default=None,
        )
        request_count = 0

        async def top(path: str, body: dict[str, object]) -> dict[str, Any]:
            nonlocal request_count
            request_count += 1
            if request_count > self.manifest.maximum_pages:
                raise ApiProviderError("connector_page_limit_exceeded")
            payload = await request_json(
                self._client,
                self.manifest,
                "POST",
                f"{_OAPI_BASE}{path}",
                params={"access_token": token},
                json=body,
            )
            return _dingtalk_result(payload)

        records: list[FrozenApiRecord] = []
        unique_records: dict[tuple[AgentEntityKind, str], FrozenApiRecord] = {}
        department_ids: list[int] = []
        queued = [root_department_id]
        visited_departments: set[int] = set()
        while queued:
            department_id = queued.pop(0)
            if department_id in visited_departments:
                continue
            visited_departments.add(department_id)
            department = await top(
                "/topapi/v2/department/get",
                {"dept_id": department_id},
            )
            department_ids.append(department_id)
            if AgentEntityKind.DEPARTMENT in selected_entities:
                record = _department_record(department_id, department)
                append_unique_record(records, unique_records, record)
            children_payload = await top(
                "/topapi/v2/department/listsub",
                {"dept_id": department_id},
            )
            children = children_payload.get(
                "items",
                children_payload.get("result", children_payload),
            )
            if isinstance(children, dict):
                children = children.get("list", [])
            if not isinstance(children, list):
                raise ApiProviderError("connector_invalid_response")
            for child in children:
                child_record = record_dict(child)
                child_id = positive_int(child_record.get("dept_id"))
                if child_id not in visited_departments:
                    queued.append(child_id)

        for department_id in department_ids:
            cursor = 0
            seen_cursors: set[int] = set()
            while True:
                if cursor in seen_cursors:
                    raise ApiProviderError("connector_pagination_incomplete")
                seen_cursors.add(cursor)
                result = await top(
                    "/topapi/v2/user/list",
                    {
                        "dept_id": department_id,
                        "cursor": cursor,
                        "size": _PAGE_SIZE,
                    },
                )
                raw_users = result.get("list", [])
                if not isinstance(raw_users, list):
                    raise ApiProviderError("connector_invalid_response")
                for item in raw_users:
                    raw = record_dict(item)
                    external_id = required_string(raw.get("userid"))
                    memberships = department_memberships(
                        raw.get("dept_id_list"),
                        fallback=department_id,
                    )
                    entity_kind = person_kind(
                        public_configuration,
                        tuple(str(value) for value in memberships),
                    )
                    if entity_kind not in selected_entities:
                        continue
                    projected, unavailable = projected_fields(
                        raw,
                        entity_kind=entity_kind,
                        number_field=number_field,
                        class_name_field=class_name_field,
                    )
                    record = FrozenApiRecord(
                        external_id=external_id,
                        entity_kind=entity_kind,
                        provider_fields=raw,
                        projected_fields=projected,
                        unavailable_fields=unavailable,
                    )
                    append_unique_record(records, unique_records, record)
                has_more = result.get("has_more", False)
                if not isinstance(has_more, bool):
                    raise ApiProviderError("connector_invalid_response")
                if not has_more:
                    break
                next_cursor = non_negative_int(result.get("next_cursor"))
                if next_cursor == cursor or next_cursor in seen_cursors:
                    raise ApiProviderError("connector_pagination_incomplete")
                cursor = next_cursor

        if not records:
            yield CapturedApiPage(page_number=1, records=(), next_cursor=None)
            return
        for start in range(0, len(records), _PAGE_SIZE):
            batch = tuple(records[start : start + _PAGE_SIZE])
            page_number = start // _PAGE_SIZE + 1
            has_next = start + _PAGE_SIZE < len(records)
            yield CapturedApiPage(
                page_number=page_number,
                records=batch,
                next_cursor=f"capture:{page_number + 1}" if has_next else None,
            )

    async def _access_token(self, secret: Mapping[str, str]) -> str:
        if set(secret) != set(self.manifest.required_secret_fields):
            raise ApiProviderError("connector_authentication_failed")
        payload = await request_json(
            self._client,
            self.manifest,
            "POST",
            _TOKEN_URL,
            bad_request_code="connector_authentication_failed",
            json={
                "appKey": secret["app_key"],
                "appSecret": secret["app_secret"],
            },
        )
        token = payload.get("accessToken")
        if not isinstance(token, str) or not token:
            raise ApiProviderError(_dingtalk_error_code(payload, authentication=True))
        return token


def _dingtalk_result(payload: dict[str, Any]) -> dict[str, Any]:
    error_code = payload.get("errcode", 0)
    if error_code not in {0, "0", None}:
        raise ApiProviderError(_dingtalk_error_code(payload))
    result = payload.get("result", {})
    if isinstance(result, list):
        return {"items": result}
    if not isinstance(result, dict):
        raise ApiProviderError("connector_invalid_response")
    return result


def _dingtalk_error_code(
    payload: Mapping[str, object],
    *,
    authentication: bool = False,
) -> str:
    raw_code = payload.get("errcode", payload.get("code", ""))
    code = str(raw_code).casefold()
    if authentication or code in {"40001", "40014", "42001"}:
        return "connector_authentication_failed"
    if code in {"403", "50004", "60011", "60020", "60121"} or any(
        marker in code for marker in ("forbidden", "permission", "unauthorized")
    ):
        return "connector_permission_denied"
    if code in {"88", "90018"} or "rate" in code or "throttl" in code:
        return "connector_rate_limited"
    return "connector_provider_rejected"


def _department_record(
    department_id: int,
    raw: Mapping[str, object],
) -> FrozenApiRecord:
    provider_fields = dict(raw)
    provider_fields.setdefault("dept_id", department_id)
    projected, unavailable = projected_fields(
        provider_fields,
        entity_kind=AgentEntityKind.DEPARTMENT,
        number_field=None,
        class_name_field=None,
    )
    return FrozenApiRecord(
        external_id=str(department_id),
        entity_kind=AgentEntityKind.DEPARTMENT,
        provider_fields=provider_fields,
        projected_fields=projected,
        unavailable_fields=unavailable,
    )
