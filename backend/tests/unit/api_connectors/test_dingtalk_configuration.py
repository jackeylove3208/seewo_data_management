import pytest

from app.api_connectors.dingtalk_configuration import (
    entity_kinds_for_scope,
    redact_server_configuration,
    validate_new_task_configuration,
)
from app.api_connectors.service import ApiConnectionValidationError
from app.schemas.agent_ingestion import AgentEntityKind


@pytest.mark.parametrize(
    ("scope", "classification_mode", "expected"),
    [
        ("department", None, (AgentEntityKind.DEPARTMENT,)),
        (
            "people",
            "organization_unit_llm",
            (AgentEntityKind.TEACHER, AgentEntityKind.STUDENT),
        ),
        (
            "all",
            "organization_unit_llm",
            (
                AgentEntityKind.DEPARTMENT,
                AgentEntityKind.TEACHER,
                AgentEntityKind.STUDENT,
            ),
        ),
    ],
)
def test_validates_new_scope_and_derives_canonical_entities(
    scope: str,
    classification_mode: str | None,
    expected: tuple[AgentEntityKind, ...],
) -> None:
    configuration: dict[str, object] = {
        "sync_scope": scope,
        "root_department_id": 1,
        "number_field": "job_number",
    }
    if classification_mode is not None:
        configuration["person_classification_mode"] = classification_mode

    normalized = validate_new_task_configuration(configuration)

    assert normalized == configuration
    assert entity_kinds_for_scope(normalized) == expected


@pytest.mark.parametrize("scope", ["", "teacher", "student", "organization"])
def test_rejects_unknown_new_scope(scope: str) -> None:
    with pytest.raises(ApiConnectionValidationError, match="同步范围"):
        validate_new_task_configuration(
            {"sync_scope": scope, "root_department_id": 1}
        )


@pytest.mark.parametrize("scope", ["people", "all"])
def test_requires_organization_classification_for_personnel_scopes(scope: str) -> None:
    with pytest.raises(ApiConnectionValidationError, match="行政单元"):
        validate_new_task_configuration(
            {"sync_scope": scope, "root_department_id": 1}
        )


def test_rejects_classification_mode_for_department_scope() -> None:
    with pytest.raises(ApiConnectionValidationError, match="部门范围"):
        validate_new_task_configuration(
            {
                "sync_scope": "department",
                "root_department_id": 1,
                "person_classification_mode": "organization_unit_llm",
            }
        )


@pytest.mark.parametrize(
    "forbidden_key",
    [
        "person_entity_kind",
        "class_name_field",
        "department_entity_kinds",
        "organization_classification",
    ],
)
def test_rejects_legacy_and_server_owned_fields(forbidden_key: str) -> None:
    with pytest.raises(ApiConnectionValidationError, match="不能提交"):
        validate_new_task_configuration(
            {
                "sync_scope": "department",
                "root_department_id": 1,
                forbidden_key: {},
            }
        )


def test_legacy_person_kind_requires_explicit_compatibility_mode() -> None:
    legacy = {"person_entity_kind": "teacher", "root_department_id": 1}

    with pytest.raises(ApiConnectionValidationError, match="同步范围"):
        entity_kinds_for_scope(legacy)

    assert entity_kinds_for_scope(legacy, allow_legacy=True) == (
        AgentEntityKind.TEACHER,
    )


def test_redacts_server_owned_classification_fields() -> None:
    public_configuration = {
        "sync_scope": "people",
        "root_department_id": 1,
        "person_classification_mode": "organization_unit_llm",
        "department_entity_kinds": {"10": "teacher"},
        "organization_classification": {"tree_fingerprint": "abc"},
    }

    assert redact_server_configuration(public_configuration) == {
        "sync_scope": "people",
        "root_department_id": 1,
        "person_classification_mode": "organization_unit_llm",
    }
