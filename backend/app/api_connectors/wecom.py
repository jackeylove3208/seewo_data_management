from collections.abc import AsyncIterator, Mapping
from typing import Any

import httpx

from app.api_connectors.contracts import (
    AgentProjectionContext,
    ApiProviderError,
    CapturedApiPage,
    ConnectionTestResult,
    FrozenApiRecord,
    ProviderManifest,
)
from app.api_connectors.provider_runtime import (
    configured_person_kinds,
    person_kind,
    project_record,
    projected_fields,
    request_json,
    require_supported_selection,
)
from app.schemas.agent_ingestion import AgentContractRecord, AgentEntityKind

WECOM_MANIFEST = ProviderManifest(
    provider_id="wecom",
    manifest_version="2026-07-29",
    adapter_version="1.0.0",
    supported_entities=frozenset(AgentEntityKind),
    required_secret_fields=("corp_id", "corp_secret"),
    required_capabilities=("contact.department.read", "contact.user.read"),
    endpoint_hosts=("qyapi.weixin.qq.com",),
    maximum_pages=10_000,
    projection_version="organization-six-fields-v1",
)

_API_BASE = "https://qyapi.weixin.qq.com/cgi-bin"
_PAGE_SIZE = 100


class WeComOrganizationAdapter:
    manifest = WECOM_MANIFEST

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def test_connection(
        self,
        public_configuration: Mapping[str, object],
        secret: Mapping[str, str],
    ) -> ConnectionTestResult:
        person_kinds = configured_person_kinds(public_configuration)
        selected = frozenset({AgentEntityKind.DEPARTMENT, *person_kinds})
        counts = {entity.value: 0 for entity in AgentEntityKind}
        async for page in self.capture(public_configuration, secret, selected):
            for record in page.records:
                counts[record.entity_kind.value] += 1
        record_count = sum(counts.values())
        capabilities = _capabilities(person_kinds)
        if record_count == 0:
            return ConnectionTestResult(
                eligible=False,
                capabilities=capabilities,
                visibility_summary={
                    "visible": False,
                    "record_count": 0,
                    **{f"{kind}_count": count for kind, count in counts.items()},
                },
                safe_error_code="connector_visibility_empty",
            )
        return ConnectionTestResult(
            eligible=True,
            capabilities=capabilities,
            visibility_summary={
                "visible": True,
                "record_count": record_count,
                **{f"{kind}_count": count for kind, count in counts.items()},
            },
        )

    async def capture(
        self,
        public_configuration: Mapping[str, object],
        secret: Mapping[str, str],
        selected_entities: frozenset[AgentEntityKind],
    ) -> AsyncIterator[CapturedApiPage]:
        require_supported_selection(public_configuration, selected_entities)
        token = await self._access_token(secret)
        number_field = _configured_field(
            public_configuration,
            "number_field",
            default=None,
        )
        class_name_field = _configured_field(
            public_configuration,
            "class_name_field",
            default=None,
        )
        root_department_id = _positive_int(
            public_configuration.get("root_department_id", 1)
        )
        request_count = 0

        async def get(path: str, params: dict[str, object]) -> dict[str, Any]:
            nonlocal request_count
            request_count += 1
            if request_count > self.manifest.maximum_pages:
                raise ApiProviderError("connector_page_limit_exceeded")
            payload = await request_json(
                self._client,
                self.manifest,
                "GET",
                f"{_API_BASE}{path}",
                params={"access_token": token, **params},
            )
            return _wecom_result(payload)

        department_payload = await get(
            "/department/list",
            {"id": root_department_id},
        )
        raw_departments = department_payload.get("department", [])
        if not isinstance(raw_departments, list):
            raise ApiProviderError("connector_invalid_response")

        records: list[FrozenApiRecord] = []
        unique_records: dict[tuple[AgentEntityKind, str], FrozenApiRecord] = {}
        department_ids: list[int] = []
        for item in raw_departments:
            raw = _record_dict(item)
            department_id = _positive_int(raw.get("id"))
            department_ids.append(department_id)
            if AgentEntityKind.DEPARTMENT in selected_entities:
                projected, unavailable = projected_fields(
                    raw,
                    entity_kind=AgentEntityKind.DEPARTMENT,
                    number_field=None,
                    class_name_field=None,
                )
                _append_unique(
                    records,
                    unique_records,
                    FrozenApiRecord(
                        external_id=str(department_id),
                        entity_kind=AgentEntityKind.DEPARTMENT,
                        provider_fields=raw,
                        projected_fields=projected,
                        unavailable_fields=unavailable,
                    ),
                )

        for department_id in department_ids:
            user_payload = await get(
                "/user/list",
                {"department_id": department_id, "fetch_child": 0},
            )
            raw_users = user_payload.get("userlist", [])
            if not isinstance(raw_users, list):
                raise ApiProviderError("connector_invalid_response")
            for item in raw_users:
                raw = _record_dict(item)
                external_id = _required_string(raw.get("userid"))
                memberships = _department_memberships(
                    raw.get("department"),
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
                _append_unique(
                    records,
                    unique_records,
                    FrozenApiRecord(
                        external_id=external_id,
                        entity_kind=entity_kind,
                        provider_fields=raw,
                        projected_fields=projected,
                        unavailable_fields=unavailable,
                    ),
                )

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

    def project(
        self,
        record: FrozenApiRecord,
        context: AgentProjectionContext,
    ) -> AgentContractRecord:
        return project_record(record, context)

    async def _access_token(self, secret: Mapping[str, str]) -> str:
        if set(secret) != set(self.manifest.required_secret_fields):
            raise ApiProviderError("connector_authentication_failed")
        payload = await request_json(
            self._client,
            self.manifest,
            "GET",
            f"{_API_BASE}/gettoken",
            bad_request_code="connector_authentication_failed",
            params={
                "corpid": secret["corp_id"],
                "corpsecret": secret["corp_secret"],
            },
        )
        result = _wecom_result(payload, authentication=True)
        token = result.get("access_token")
        if not isinstance(token, str) or not token:
            raise ApiProviderError("connector_authentication_failed")
        return token


def _wecom_result(
    payload: dict[str, Any],
    *,
    authentication: bool = False,
) -> dict[str, Any]:
    error_code = payload.get("errcode", 0)
    if error_code not in {0, "0", None}:
        raise ApiProviderError(
            _wecom_error_code(error_code, authentication=authentication)
        )
    return payload


def _wecom_error_code(value: object, *, authentication: bool) -> str:
    code = str(value)
    if authentication or code in {"40001", "40013", "40014", "42001"}:
        return "connector_authentication_failed"
    if code in {"48002", "60011", "60020", "84014", "301002"}:
        return "connector_permission_denied"
    if code in {"45009"}:
        return "connector_rate_limited"
    return "connector_provider_rejected"


def _append_unique(
    records: list[FrozenApiRecord],
    unique_records: dict[tuple[AgentEntityKind, str], FrozenApiRecord],
    record: FrozenApiRecord,
) -> None:
    key = (record.entity_kind, record.external_id)
    existing = unique_records.get(key)
    if existing is None:
        unique_records[key] = record
        records.append(record)
        return
    if existing != record:
        raise ApiProviderError("connector_duplicate_external_id")


def _configured_field(
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


def _department_memberships(value: object, *, fallback: int) -> tuple[int, ...]:
    if value is None:
        return (fallback,)
    if not isinstance(value, list):
        raise ApiProviderError("connector_invalid_response")
    return tuple(_positive_int(item) for item in value) or (fallback,)


def _record_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ApiProviderError("connector_invalid_response")
    return dict(value)


def _required_string(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ApiProviderError("connector_invalid_response")
    return value


def _positive_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ApiProviderError("connector_invalid_response")
    try:
        result = int(value)
    except ValueError as error:
        raise ApiProviderError("connector_invalid_response") from error
    if result <= 0:
        raise ApiProviderError("connector_invalid_response")
    return result


def _capabilities(
    person_kinds: frozenset[AgentEntityKind],
) -> dict[str, bool]:
    return {
        "organization.read": True,
        "entity.department.read": True,
        "entity.student.read": AgentEntityKind.STUDENT in person_kinds,
        "entity.teacher.read": AgentEntityKind.TEACHER in person_kinds,
    }
