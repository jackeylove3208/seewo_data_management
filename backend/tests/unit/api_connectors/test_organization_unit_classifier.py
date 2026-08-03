import json

import pytest

from app.ai.providers.base import LLMRequest, LLMResponse
from app.api_connectors.contracts import OrganizationInspection, OrganizationUnitNode
from app.api_connectors.organization_unit_classifier import (
    DingTalkOrganizationUnitClassifier,
    OrganizationClassificationError,
)


class SequencedProvider:
    def __init__(self, outputs: list[dict[str, object]]) -> None:
        self.outputs = outputs
        self.requests: list[LLMRequest] = []

    async def complete_json_once(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return LLMResponse(
            output=self.outputs[len(self.requests) - 1],
            provider="stub-provider",
            model="stub-model",
            request_id=f"request-{len(self.requests)}",
        )


def _inspection(
    *,
    memberships: tuple[tuple[str, ...], ...] = (("11",), ("22",)),
) -> OrganizationInspection:
    nodes = (
        OrganizationUnitNode(
            department_id="1", name="示例学校", parent_id=None, path=("示例学校",)
        ),
        OrganizationUnitNode(
            department_id="10",
            name="教职工",
            parent_id="1",
            path=("示例学校", "教职工"),
        ),
        OrganizationUnitNode(
            department_id="11",
            name="数学组",
            parent_id="10",
            path=("示例学校", "教职工", "数学组"),
        ),
        OrganizationUnitNode(
            department_id="20",
            name="学生",
            parent_id="1",
            path=("示例学校", "学生"),
        ),
        OrganizationUnitNode(
            department_id="21",
            name="七年级",
            parent_id="20",
            path=("示例学校", "学生", "七年级"),
        ),
        OrganizationUnitNode(
            department_id="22",
            name="一班",
            parent_id="21",
            path=("示例学校", "学生", "七年级", "一班"),
        ),
    )
    return OrganizationInspection(
        departments=nodes,
        personnel_department_ids=frozenset(
            department_id for group in memberships for department_id in group
        ),
        personnel_memberships=memberships,
        visible_person_count=len(memberships),
        tree_fingerprint="a" * 64,
    )


def _output(**overrides: str) -> dict[str, object]:
    labels = {
        "1": "unknown",
        "10": "teacher",
        "11": "unknown",
        "20": "student",
        "21": "unknown",
        "22": "unknown",
        **overrides,
    }
    return {
        "result": {
            "classifications": [
                {"department_id": department_id, "entity_kind": entity_kind}
                for department_id, entity_kind in labels.items()
            ]
        }
    }


async def test_classifies_branches_and_expands_ancestor_inheritance() -> None:
    provider = SequencedProvider([_output()])

    result = await DingTalkOrganizationUnitClassifier(provider).classify(_inspection())

    assert result.department_entity_kinds == {
        "10": "teacher",
        "11": "teacher",
        "20": "student",
        "21": "student",
        "22": "student",
    }
    assert result.skill_version == "1.0.0"
    assert len(result.input_hash) == len(result.output_hash) == 64
    assert result.attempts[0].request_id == "request-1"
    assert result.attempts[0].outcome == "accepted"
    request_text = provider.requests[0].messages[1].content
    assert "department_id" in request_text
    assert "personnel_memberships" not in request_text
    assert "visible_person_count" not in request_text


async def test_repairs_invented_ids_before_accepting_exact_membership() -> None:
    invented = _output()
    classifications = invented["result"]["classifications"]
    assert isinstance(classifications, list)
    classifications.append({"department_id": "999", "entity_kind": "teacher"})
    provider = SequencedProvider([invented, _output()])

    result = await DingTalkOrganizationUnitClassifier(provider).classify(_inspection())

    assert len(provider.requests) == 2
    assert [attempt.outcome for attempt in result.attempts] == ["rejected", "accepted"]
    repair_payload = json.loads(provider.requests[1].messages[-1].content)
    assert repair_payload["validation_errors"]


async def test_rejects_descendant_override_after_three_attempts() -> None:
    provider = SequencedProvider([_output(**{"11": "student"})] * 3)

    with pytest.raises(OrganizationClassificationError) as captured:
        await DingTalkOrganizationUnitClassifier(provider).classify(_inspection())

    assert captured.value.safe_code == "connector_entity_classification_invalid"
    assert len(provider.requests) == 3


async def test_rejects_unknown_personnel_branch_with_safe_path() -> None:
    provider = SequencedProvider([_output(**{"10": "unknown"})] * 3)

    with pytest.raises(OrganizationClassificationError) as captured:
        await DingTalkOrganizationUnitClassifier(provider).classify(_inspection())

    assert captured.value.safe_code == "connector_entity_classification_unknown"
    assert captured.value.issue_paths == (("示例学校", "教职工", "数学组"),)


async def test_rejects_membership_across_teacher_and_student_branches() -> None:
    provider = SequencedProvider([_output()])

    with pytest.raises(OrganizationClassificationError) as captured:
        await DingTalkOrganizationUnitClassifier(provider).classify(
            _inspection(memberships=(("11", "22"),))
        )

    assert captured.value.safe_code == "connector_entity_classification_ambiguous"
    assert len(provider.requests) == 1
