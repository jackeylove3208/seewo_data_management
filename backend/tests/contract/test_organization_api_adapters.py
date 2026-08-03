import json
from collections.abc import Callable
from urllib.parse import parse_qs

import httpx
import pytest

from app.api_connectors.contracts import ApiProviderError
from app.api_connectors.dingtalk import DingtalkOrganizationAdapter
from app.api_connectors.wecom import WeComOrganizationAdapter
from app.schemas.agent_ingestion import AgentEntityKind

Handler = Callable[[httpx.Request], httpx.Response]


def _dingtalk_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/v1.0/oauth2/accessToken":
        assert json.loads(request.content) == {
            "appKey": "ding-app",
            "appSecret": "ding-secret",
        }
        return httpx.Response(200, json={"accessToken": "ding-token", "expireIn": 7200})
    assert parse_qs(request.url.query.decode())["access_token"] == ["ding-token"]
    if request.url.path == "/topapi/v2/department/get":
        return httpx.Response(
            200,
            json={
                "errcode": 0,
                "result": {"dept_id": 1, "name": "示例学校", "parent_id": 0},
            },
        )
    if request.url.path == "/topapi/v2/department/listsub":
        return httpx.Response(
            200,
            json={
                "errcode": 0,
                "result": [
                    {"dept_id": 21, "name": "小学部", "parent_id": 1},
                ],
            },
        )
    if request.url.path == "/topapi/v2/user/list":
        body = json.loads(request.content)
        if body["dept_id"] == 1:
            return httpx.Response(
                200,
                json={
                    "errcode": 0,
                    "result": {"has_more": False, "list": [], "next_cursor": 0},
                },
            )
        if body["cursor"] == 0:
            return httpx.Response(
                200,
                json={
                    "errcode": 0,
                    "result": {
                        "has_more": True,
                        "next_cursor": 100,
                        "list": [
                            {
                                "userid": "teacher/42",
                                "name": "周明远",
                                "job_number": "T2026042",
                                "mobile": "13800000042",
                                "email": "zhou@example.test",
                                "dept_id_list": [21],
                            }
                        ],
                    },
                },
            )
        return httpx.Response(
            200,
            json={
                "errcode": 0,
                "result": {
                    "has_more": False,
                    "next_cursor": 100,
                    "list": [
                        {
                            "userid": "teacher-43",
                            "name": "叶舒桐",
                            "job_number": "T2026043",
                            "mobile": "",
                            "email": "",
                            "dept_id_list": [21],
                        }
                    ],
                },
            },
        )
    raise AssertionError(f"unexpected DingTalk path: {request.url.path}")


def _wecom_handler(request: httpx.Request) -> httpx.Response:
    query = parse_qs(request.url.query.decode())
    if request.url.path == "/cgi-bin/gettoken":
        assert query == {"corpid": ["wx-corp"], "corpsecret": ["wx-secret"]}
        return httpx.Response(
            200,
            json={"errcode": 0, "errmsg": "ok", "access_token": "wx-token"},
        )
    assert query["access_token"] == ["wx-token"]
    if request.url.path == "/cgi-bin/department/list":
        return httpx.Response(
            200,
            json={
                "errcode": 0,
                "errmsg": "ok",
                "department": [
                    {"id": 1, "name": "示例学校", "parentid": 0, "order": 1},
                    {"id": 21, "name": "小学部", "parentid": 1, "order": 2},
                ],
            },
        )
    if request.url.path == "/cgi-bin/user/list":
        if query["department_id"] == ["1"]:
            return httpx.Response(200, json={"errcode": 0, "errmsg": "ok", "userlist": []})
        return httpx.Response(
            200,
            json={
                "errcode": 0,
                "errmsg": "ok",
                "userlist": [
                    {
                        "userid": "teacher/42",
                        "name": "周明远",
                        "mobile": "13800000042",
                        "email": "zhou@example.test",
                        "department": [21],
                    }
                ],
            },
        )
    raise AssertionError(f"unexpected WeCom path: {request.url.path}")


def _adapter(
    provider_id: str,
    handler: Handler,
) -> tuple[DingtalkOrganizationAdapter | WeComOrganizationAdapter, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    if provider_id == "dingtalk":
        return DingtalkOrganizationAdapter(client), client
    return WeComOrganizationAdapter(client), client


def _secret(provider_id: str) -> dict[str, str]:
    if provider_id == "dingtalk":
        return {"app_key": "ding-app", "app_secret": "ding-secret"}
    return {"corp_id": "wx-corp", "corp_secret": "wx-secret"}


@pytest.mark.parametrize(
    ("provider_id", "handler"),
    [("dingtalk", _dingtalk_handler), ("wecom", _wecom_handler)],
)
async def test_adapter_contract_closes_pagination_and_keeps_external_id_out_of_number(
    provider_id: str,
    handler: Handler,
) -> None:
    adapter, client = _adapter(provider_id, handler)
    try:
        pages = [
            page
            async for page in adapter.capture(
                public_configuration={
                    "organization_ref": "school-1",
                    "person_entity_kind": "teacher",
                    **({"number_field": "job_number"} if provider_id == "dingtalk" else {}),
                },
                secret=_secret(provider_id),
                selected_entities=frozenset(
                    {AgentEntityKind.DEPARTMENT, AgentEntityKind.TEACHER}
                ),
            )
        ]
    finally:
        await client.aclose()

    assert pages
    assert pages[-1].next_cursor is None
    assert tuple(page.page_number for page in pages) == tuple(range(1, len(pages) + 1))
    teacher = next(
        record
        for page in pages
        for record in page.records
        if record.entity_kind is AgentEntityKind.TEACHER
    )
    assert teacher.projected_fields["number"] != teacher.external_id
    assert teacher.projected_fields["name"] == "周明远"


@pytest.mark.parametrize(
    ("provider_id", "handler"),
    [("dingtalk", _dingtalk_handler), ("wecom", _wecom_handler)],
)
async def test_adapter_connection_probe_reports_visible_capabilities(
    provider_id: str,
    handler: Handler,
) -> None:
    adapter, client = _adapter(provider_id, handler)
    try:
        result = await adapter.test_connection(
            {"organization_ref": "school-1", "person_entity_kind": "teacher"},
            _secret(provider_id),
        )
    finally:
        await client.aclose()

    assert result.eligible is True
    assert result.capabilities["organization.read"] is True
    assert result.capabilities["entity.teacher.read"] is True
    assert result.visibility_summary["visible"] is True


@pytest.mark.parametrize("provider_id", ["dingtalk", "wecom"])
async def test_adapter_translates_timeout_without_exposing_request(
    provider_id: str,
) -> None:
    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("request included a credential", request=request)

    adapter, client = _adapter(provider_id, timeout_handler)
    try:
        with pytest.raises(ApiProviderError) as captured:
            await adapter.test_connection({}, _secret(provider_id))
    finally:
        await client.aclose()

    assert captured.value.safe_code == "connector_timeout"
    assert "credential" not in str(captured.value)


@pytest.mark.parametrize("provider_id", ["dingtalk", "wecom"])
async def test_adapter_rejects_undistinguished_student_selection(
    provider_id: str,
) -> None:
    handler = _dingtalk_handler if provider_id == "dingtalk" else _wecom_handler
    adapter, client = _adapter(provider_id, handler)
    try:
        with pytest.raises(ApiProviderError) as captured:
            _ = [
                page
                async for page in adapter.capture(
                    {"person_entity_kind": "teacher"},
                    _secret(provider_id),
                    frozenset({AgentEntityKind.STUDENT}),
                )
            ]
    finally:
        await client.aclose()

    assert captured.value.safe_code == "connector_entity_unsupported"


@pytest.mark.parametrize(
    ("provider_id", "error_response"),
    [
        ("dingtalk", httpx.Response(403, json={"message": "secret provider body"})),
        (
            "wecom",
            httpx.Response(
                200,
                json={"errcode": 48002, "errmsg": "api forbidden with secret detail"},
            ),
        ),
    ],
)
async def test_adapter_translates_permission_denial_to_safe_code(
    provider_id: str,
    error_response: httpx.Response,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1.0/oauth2/accessToken":
            return httpx.Response(200, json={"accessToken": "ding-token", "expireIn": 7200})
        if request.url.path == "/cgi-bin/gettoken":
            return httpx.Response(
                200,
                json={"errcode": 0, "errmsg": "ok", "access_token": "wx-token"},
            )
        return error_response

    adapter, client = _adapter(provider_id, handler)
    try:
        with pytest.raises(ApiProviderError) as captured:
            await adapter.test_connection({}, _secret(provider_id))
    finally:
        await client.aclose()

    assert captured.value.safe_code == "connector_permission_denied"
    assert "secret detail" not in str(captured.value)


def _dingtalk_repeated_cursor_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path != "/topapi/v2/user/list":
        return _dingtalk_handler(request)
    return httpx.Response(
        200,
        json={
            "errcode": 0,
            "result": {
                "has_more": True,
                "next_cursor": 0,
                "list": [
                    {
                        "userid": "teacher-1",
                        "name": "重复游标",
                        "dept_id_list": [1],
                    }
                ],
            },
        },
    )


async def test_dingtalk_adapter_rejects_repeated_cursor() -> None:
    adapter, client = _adapter("dingtalk", _dingtalk_repeated_cursor_handler)
    try:
        with pytest.raises(ApiProviderError) as captured:
            _ = [
                page
                async for page in adapter.capture(
                    {"person_entity_kind": "teacher"},
                    _secret("dingtalk"),
                    frozenset({AgentEntityKind.TEACHER}),
                )
            ]
    finally:
        await client.aclose()

    assert captured.value.safe_code == "connector_pagination_incomplete"


def _dingtalk_hierarchy_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/v1.0/oauth2/accessToken":
        return httpx.Response(200, json={"accessToken": "ding-token"})
    body = json.loads(request.content)
    departments = {
        1: {"dept_id": 1, "name": "示例学校", "parent_id": 0},
        10: {"dept_id": 10, "name": "教职工", "parent_id": 1},
        11: {"dept_id": 11, "name": "数学组", "parent_id": 10},
        20: {"dept_id": 20, "name": "学生", "parent_id": 1},
        21: {"dept_id": 21, "name": "七年级", "parent_id": 20},
        22: {"dept_id": 22, "name": "一班", "parent_id": 21},
    }
    children = {1: [10, 20], 10: [11], 20: [21], 21: [22]}
    if request.url.path == "/topapi/v2/department/get":
        return httpx.Response(
            200,
            json={"errcode": 0, "result": departments[body["dept_id"]]},
        )
    if request.url.path == "/topapi/v2/department/listsub":
        return httpx.Response(
            200,
            json={
                "errcode": 0,
                "result": [
                    departments[department_id]
                    for department_id in children.get(body["dept_id"], [])
                ],
            },
        )
    if request.url.path == "/topapi/v2/user/list":
        users = {
            11: [
                {
                    "userid": "staff-1",
                    "name": "合成教师",
                    "mobile": "13800000000",
                    "email": "staff@example.test",
                    "dept_id_list": [11],
                }
            ],
            22: [
                {
                    "userid": "student-1",
                    "name": "合成学生",
                    "mobile": "13900000000",
                    "email": "student@example.test",
                    "dept_id_list": [22],
                }
            ],
        }
        return httpx.Response(
            200,
            json={
                "errcode": 0,
                "result": {
                    "has_more": False,
                    "next_cursor": 0,
                    "list": users.get(body["dept_id"], []),
                },
            },
        )
    raise AssertionError(f"unexpected DingTalk path: {request.url.path}")


async def test_dingtalk_inspection_returns_only_safe_hierarchy_and_memberships() -> None:
    adapter, client = _adapter("dingtalk", _dingtalk_hierarchy_handler)
    try:
        inspection = await adapter.inspect_organization(
            {"sync_scope": "people", "root_department_id": 1},
            _secret("dingtalk"),
        )
    finally:
        await client.aclose()

    nodes = {node.department_id: node for node in inspection.departments}
    assert set(nodes) == {"1", "10", "11", "20", "21", "22"}
    assert nodes["11"].parent_id == "10"
    assert nodes["11"].path == ("示例学校", "教职工", "数学组")
    assert nodes["22"].path == ("示例学校", "学生", "七年级", "一班")
    assert inspection.personnel_department_ids == frozenset({"11", "22"})
    assert inspection.personnel_memberships == (("11",), ("22",))
    assert inspection.visible_person_count == 2
    assert len(inspection.tree_fingerprint) == 64
    serialized = inspection.model_dump_json()
    assert "staff-1" not in serialized
    assert "student-1" not in serialized
    assert "13800000000" not in serialized
    assert "staff@example.test" not in serialized


async def test_dingtalk_department_capture_never_reads_people() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/topapi/v2/user/list":
            raise AssertionError("department capture must not read people")
        return _dingtalk_hierarchy_handler(request)

    adapter, client = _adapter("dingtalk", handler)
    try:
        pages = [
            page
            async for page in adapter.capture(
                {"sync_scope": "department", "root_department_id": 1},
                _secret("dingtalk"),
                frozenset({AgentEntityKind.DEPARTMENT}),
            )
        ]
    finally:
        await client.aclose()

    assert sum(len(page.records) for page in pages) == 6


async def test_dingtalk_capture_rejects_changed_classified_tree() -> None:
    adapter, client = _adapter("dingtalk", _dingtalk_hierarchy_handler)
    try:
        with pytest.raises(ApiProviderError) as captured:
            _ = [
                page
                async for page in adapter.capture(
                    {
                        "sync_scope": "people",
                        "person_classification_mode": "organization_unit_llm",
                        "root_department_id": 1,
                        "department_entity_kinds": {
                            "10": "teacher",
                            "11": "teacher",
                            "20": "student",
                            "21": "student",
                            "22": "student",
                        },
                        "organization_classification": {
                            "tree_fingerprint": "b" * 64
                        },
                    },
                    _secret("dingtalk"),
                    frozenset(
                        {AgentEntityKind.TEACHER, AgentEntityKind.STUDENT}
                    ),
                )
            ]
    finally:
        await client.aclose()

    assert captured.value.safe_code == "connector_organization_changed"


def _wecom_duplicate_user_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path != "/cgi-bin/user/list":
        return _wecom_handler(request)
    return httpx.Response(
        200,
        json={
            "errcode": 0,
            "errmsg": "ok",
            "userlist": [
                {"userid": "duplicate", "name": "第一条", "department": [1]},
                {"userid": "duplicate", "name": "第二条", "department": [1]},
            ],
        },
    )


async def test_wecom_adapter_rejects_conflicting_external_ids() -> None:
    adapter, client = _adapter("wecom", _wecom_duplicate_user_handler)
    try:
        with pytest.raises(ApiProviderError) as captured:
            _ = [
                page
                async for page in adapter.capture(
                    {"person_entity_kind": "teacher"},
                    _secret("wecom"),
                    frozenset({AgentEntityKind.TEACHER}),
                )
            ]
    finally:
        await client.aclose()

    assert captured.value.safe_code == "connector_duplicate_external_id"
