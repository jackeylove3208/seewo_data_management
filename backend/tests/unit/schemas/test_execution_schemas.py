from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.schemas.canonical_entities import EntityType
from app.schemas.executions import (
    GovernanceOperation,
    OperationType,
    ProposalSource,
    ProposalVersionRef,
)
from app.schemas.governance import RiskLevel


def proposal_ref() -> ProposalVersionRef:
    return ProposalVersionRef(proposal_id=uuid4(), proposal_version=1)


def operation_payload(operation_type: OperationType) -> dict[str, object]:
    payload: dict[str, object] = {
        "proposal": proposal_ref(),
        "proposal_source": ProposalSource.AI,
        "difference_id": uuid4(),
        "difference_version": 2,
        "operation_type": operation_type,
        "entity_type": EntityType.TEACHER,
        "changed_fields": frozenset({"name"}),
        "dependencies": frozenset({uuid4()}),
        "reversible": True,
        "risk": RiskLevel.MEDIUM,
    }
    if operation_type is OperationType.CREATE:
        payload["after"] = {"name": "Corrected teacher"}
    elif operation_type is OperationType.SKIP:
        payload.update(
            before={"name": "Existing teacher"},
            changed_fields=frozenset(),
            reversible=False,
            risk=RiskLevel.LOW,
        )
    else:
        payload.update(
            target_entity_id=uuid4(),
            target_source_identifier="teacher-17",
            before={"name": "Existing teacher"},
            after={"name": "Corrected teacher"},
        )
    return payload


def test_operation_type_contains_only_executable_operations() -> None:
    assert {operation.value for operation in OperationType} == {
        "create",
        "update",
        "move",
        "disable",
        "skip",
    }
    with pytest.raises(ValueError):
        OperationType("manual_review")
    with pytest.raises(ValueError):
        OperationType("delete")


def test_proposal_version_ref_is_strict_frozen_and_positive() -> None:
    reference = proposal_ref()

    with pytest.raises(ValidationError):
        ProposalVersionRef(proposal_id=str(reference.proposal_id), proposal_version=1)
    with pytest.raises(ValidationError):
        ProposalVersionRef(proposal_id=uuid4(), proposal_version=0)
    with pytest.raises(ValidationError):
        ProposalVersionRef(proposal_id=uuid4(), proposal_version=1, unexpected=True)
    with pytest.raises(ValidationError):
        reference.proposal_version = 2


def test_governance_operation_preserves_versioned_execution_facts() -> None:
    payload = operation_payload(OperationType.UPDATE)
    operation = GovernanceOperation(**payload)

    assert operation.proposal == payload["proposal"]
    assert operation.proposal_source is ProposalSource.AI
    assert operation.difference_id == payload["difference_id"]
    assert operation.difference_version == 2
    assert operation.target_entity_id == payload["target_entity_id"]
    assert operation.target_source_identifier == "teacher-17"
    assert operation.before == {"name": "Existing teacher"}
    assert operation.after == {"name": "Corrected teacher"}
    assert operation.changed_fields == frozenset({"name"})
    assert operation.dependencies == payload["dependencies"]
    assert operation.reversible is True
    assert operation.risk is RiskLevel.MEDIUM
    with pytest.raises(ValidationError):
        operation.risk = RiskLevel.HIGH


@pytest.mark.parametrize("fact_field", ["before", "after"])
def test_governance_operation_rejects_top_level_fact_mutation(fact_field: str) -> None:
    operation = GovernanceOperation(**operation_payload(OperationType.UPDATE))
    facts = getattr(operation, fact_field)

    assert facts is not None
    with pytest.raises(TypeError):
        facts["name"] = "Mutated teacher"


def test_governance_operation_rejects_nested_fact_mutation() -> None:
    payload = operation_payload(OperationType.UPDATE)
    payload["before"] = {
        "profile": {"name": "Existing teacher"},
        "assignments": ["class-a", "class-b"],
    }
    payload["after"] = {
        "profile": {"name": "Corrected teacher"},
        "assignments": ["class-a", "class-c"],
    }
    operation = GovernanceOperation(**payload)

    assert operation.before is not None
    assert operation.after is not None
    with pytest.raises(TypeError):
        operation.before["profile"]["name"] = "Mutated teacher"
    with pytest.raises(TypeError):
        operation.after["assignments"][0] = "class-z"


def test_governance_operation_dumps_facts_as_json_containers() -> None:
    payload = operation_payload(OperationType.UPDATE)
    payload["before"] = {
        "profile": {"name": "Existing teacher"},
        "assignments": ["class-a", "class-b"],
    }
    payload["after"] = {
        "profile": {"name": "Corrected teacher"},
        "assignments": ["class-a", "class-c"],
    }
    dumped = GovernanceOperation(**payload).model_dump(mode="json")

    assert dumped["before"] == payload["before"]
    assert dumped["after"] == payload["after"]
    assert type(dumped["before"]) is dict
    assert type(dumped["before"]["profile"]) is dict
    assert type(dumped["before"]["assignments"]) is list


@pytest.mark.parametrize(
    "updates",
    [
        {"before": {"name": "Unexpected existing teacher"}},
        {"after": None},
        {"target_entity_id": uuid4()},
        {"target_source_identifier": "teacher-17"},
    ],
)
def test_create_rejects_existing_target_or_missing_after(updates: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        GovernanceOperation(**{**operation_payload(OperationType.CREATE), **updates})


@pytest.mark.parametrize(
    "operation_type",
    [OperationType.UPDATE, OperationType.MOVE, OperationType.DISABLE],
)
@pytest.mark.parametrize(
    "updates",
    [
        {"target_entity_id": None, "target_source_identifier": None},
        {"before": None},
        {"after": None},
    ],
)
def test_mutation_of_existing_target_requires_target_and_expected_facts(
    operation_type: OperationType,
    updates: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        GovernanceOperation(**{**operation_payload(operation_type), **updates})


def test_target_mutation_can_use_connector_identifier_without_internal_uuid() -> None:
    payload = operation_payload(OperationType.MOVE)
    payload["target_entity_id"] = None

    operation = GovernanceOperation(**payload)

    assert operation.target_entity_id is None
    assert operation.target_source_identifier == "teacher-17"


@pytest.mark.parametrize(
    "updates",
    [
        {"changed_fields": frozenset({"name"})},
        {"after": {"name": "Changed teacher"}},
    ],
)
def test_skip_is_non_mutating(updates: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        GovernanceOperation(**{**operation_payload(OperationType.SKIP), **updates})


def test_governance_operation_rejects_invalid_versions_and_extra_fields() -> None:
    payload = operation_payload(OperationType.CREATE)

    with pytest.raises(ValidationError):
        GovernanceOperation(**{**payload, "difference_version": 0})
    with pytest.raises(ValidationError):
        GovernanceOperation(**{**payload, "unexpected": True})


def test_governance_operation_uses_uuid_identity() -> None:
    operation = GovernanceOperation(**operation_payload(OperationType.CREATE))

    assert isinstance(operation.id, UUID)
