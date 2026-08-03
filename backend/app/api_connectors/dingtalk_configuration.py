from collections.abc import Mapping
from typing import Literal

from app.schemas.agent_ingestion import AgentEntityKind

DingTalkSyncScope = Literal["department", "people", "all"]

SERVER_CONFIGURATION_KEYS = frozenset(
    {
        "department_entity_kinds",
        "person_membership_entity_kinds",
        "organization_classification",
    }
)
_CLIENT_FORBIDDEN_KEYS = SERVER_CONFIGURATION_KEYS | frozenset(
    {"person_entity_kind", "class_name_field"}
)
_SCOPE_ENTITY_KINDS: dict[str, tuple[AgentEntityKind, ...]] = {
    "department": (AgentEntityKind.DEPARTMENT,),
    "people": (AgentEntityKind.TEACHER, AgentEntityKind.STUDENT),
    "all": (
        AgentEntityKind.DEPARTMENT,
        AgentEntityKind.TEACHER,
        AgentEntityKind.STUDENT,
    ),
}


class ApiConnectionValidationError(ValueError):
    pass


def validate_new_task_configuration(
    configuration: Mapping[str, object],
) -> dict[str, object]:
    forbidden = _CLIENT_FORBIDDEN_KEYS.intersection(configuration)
    if forbidden:
        raise ApiConnectionValidationError(
            f"钉钉连接不能提交字段：{', '.join(sorted(forbidden))}"
        )
    scope = configuration.get("sync_scope")
    if scope not in _SCOPE_ENTITY_KINDS:
        raise ApiConnectionValidationError(
            "钉钉同步范围必须是部门、人员或全部"
        )
    root_department_id = configuration.get("root_department_id")
    if (
        isinstance(root_department_id, bool)
        or not isinstance(root_department_id, int)
        or root_department_id <= 0
    ):
        raise ApiConnectionValidationError("钉钉根部门 ID 必须是正整数")
    classification_mode = configuration.get("person_classification_mode")
    if scope == "department" and classification_mode is not None:
        raise ApiConnectionValidationError("钉钉部门范围不能配置人员分类模式")
    if scope in {"people", "all"} and classification_mode != "organization_unit_llm":
        raise ApiConnectionValidationError("钉钉人员范围必须使用行政单元分类")
    number_field = configuration.get("number_field")
    if number_field is not None and (
        not isinstance(number_field, str) or not number_field.strip()
    ):
        raise ApiConnectionValidationError("钉钉 number_field 必须是非空字符串")
    return dict(configuration)


def entity_kinds_for_scope(
    configuration: Mapping[str, object],
    *,
    allow_legacy: bool = False,
) -> tuple[AgentEntityKind, ...]:
    scope = configuration.get("sync_scope")
    if isinstance(scope, str) and scope in _SCOPE_ENTITY_KINDS:
        return _SCOPE_ENTITY_KINDS[scope]
    if allow_legacy:
        legacy = configuration.get("person_entity_kind")
        if legacy in {AgentEntityKind.TEACHER.value, AgentEntityKind.STUDENT.value}:
            return (AgentEntityKind(str(legacy)),)
    raise ApiConnectionValidationError("钉钉同步范围配置无效")


def redact_server_configuration(
    configuration: Mapping[str, object],
) -> dict[str, object]:
    return {
        key: value
        for key, value in configuration.items()
        if key not in SERVER_CONFIGURATION_KEYS
    }
