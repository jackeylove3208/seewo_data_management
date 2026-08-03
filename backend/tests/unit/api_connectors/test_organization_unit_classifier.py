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
        output = self.outputs[len(self.requests)]
        self.requests.append(request)
        return LLMResponse(
            output=output,
            provider="stub-provider",
            model="stub-model",
            request_id=f"request-{len(self.requests)}",
        )


def _explicit_inspection() -> OrganizationInspection:
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
            department_id="22",
            name="一班",
            parent_id="20",
            path=("示例学校", "学生", "一班"),
        ),
    )
    memberships = (("1", "11"), ("1", "22"))
    return OrganizationInspection(
        departments=nodes,
        personnel_department_ids=frozenset(
            department_id for group in memberships for department_id in group
        ),
        personnel_memberships=memberships,
        visible_person_count=2,
        tree_fingerprint="a" * 64,
    )


def _unresolved_inspection(
    *, memberships: tuple[tuple[str, ...], ...] = (("30",),)
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
            department_id="20",
            name="学生",
            parent_id="1",
            path=("示例学校", "学生"),
        ),
        OrganizationUnitNode(
            department_id="30",
            name="综合中心",
            parent_id="1",
            path=("示例学校", "综合中心"),
        ),
    )
    return OrganizationInspection(
        departments=nodes,
        personnel_department_ids=frozenset(
            department_id for group in memberships for department_id in group
        ),
        personnel_memberships=memberships,
        visible_person_count=len(memberships),
        tree_fingerprint="b" * 64,
    )


def _membership_output(
    *items: tuple[str, str],
) -> dict[str, object]:
    return {
        "result": {
            "classifications": [
                {"membership_key": key, "entity_kind": kind}
                for key, kind in items
            ]
        }
    }


async def test_explicit_memberships_ignore_neutral_root_and_skip_model() -> None:
    provider = SequencedProvider([])

    result = await DingTalkOrganizationUnitClassifier(provider).classify(
        _explicit_inspection()
    )

    assert result.department_entity_kinds == {
        "10": "teacher",
        "11": "teacher",
        "20": "student",
        "22": "student",
    }
    assert result.person_membership_entity_kinds == {
        "1|11": "teacher",
        "1|22": "student",
    }
    assert result.attempts == ()
    assert provider.requests == []


@pytest.mark.parametrize(
    ("memberships", "membership_key", "decision"),
    [
        ((("30",),), "30", "teacher"),
        ((("10", "20"),), "10|20", "student"),
    ],
)
async def test_llm_makes_binary_decision_for_neither_or_both_kinds(
    memberships: tuple[tuple[str, ...], ...],
    membership_key: str,
    decision: str,
) -> None:
    provider = SequencedProvider([_membership_output((membership_key, decision))])

    result = await DingTalkOrganizationUnitClassifier(provider).classify(
        _unresolved_inspection(memberships=memberships)
    )

    assert result.person_membership_entity_kinds == {membership_key: decision}
    assert set(result.person_membership_entity_kinds.values()) <= {
        "teacher",
        "student",
    }
    request_text = provider.requests[0].messages[1].content
    assert membership_key in request_text
    assert "visible_person_count" not in request_text
    assert "合成人员" not in request_text


async def test_repairs_incomplete_membership_output_before_accepting() -> None:
    inspection = _unresolved_inspection(memberships=(("30",), ("10", "20")))
    provider = SequencedProvider(
        [
            _membership_output(("30", "teacher")),
            _membership_output(("30", "teacher"), ("10|20", "student")),
        ]
    )

    result = await DingTalkOrganizationUnitClassifier(provider).classify(inspection)

    assert len(provider.requests) == 2
    assert [attempt.outcome for attempt in result.attempts] == [
        "rejected",
        "accepted",
    ]
    repair_payload = json.loads(provider.requests[1].messages[-1].content)
    assert repair_payload["validation_errors"]


async def test_rejects_non_binary_or_invented_membership_after_three_attempts() -> None:
    provider = SequencedProvider(
        [_membership_output(("invented", "teacher"))] * 3
    )

    with pytest.raises(OrganizationClassificationError) as captured:
        await DingTalkOrganizationUnitClassifier(provider).classify(
            _unresolved_inspection()
        )

    assert captured.value.safe_code == "connector_entity_classification_invalid"
    assert len(provider.requests) == 3
