from uuid import uuid4

import pytest

from app.ai.agent_analysis import AgentModelOutputError, validate_agent_model_output


def _output(work_item_id):
    return {
        "findings": [
            {
                "work_item_id": str(work_item_id),
                "kind": "target_extra",
                "category_zh": "希沃多余",
                "analysis_zh": "第三方不存在对应身份键。",
                "evidence_refs": ["input:csv:2"],
                "solutions": [
                    {
                        "operation": "delete",
                        "risk": "high",
                        "solution_zh": "删除希沃多余记录。",
                        "recommended": True,
                    }
                ],
            }
        ]
    }


def test_accepts_exact_complete_model_membership() -> None:
    work_item_id = uuid4()

    findings = validate_agent_model_output(_output(work_item_id), (work_item_id,))

    assert findings[0].work_item_id == work_item_id


def test_rejects_omitted_or_forged_work_items() -> None:
    work_item_id = uuid4()

    with pytest.raises(AgentModelOutputError, match="exactly"):
        validate_agent_model_output(_output(work_item_id), (uuid4(),))


def test_rejects_authority_write_solution() -> None:
    work_item_id = uuid4()
    output = _output(work_item_id)
    output["findings"][0]["kind"] = "authority_invalid"
    output["findings"][0]["solutions"][0]["operation"] = "update"

    with pytest.raises(AgentModelOutputError, match="authority"):
        validate_agent_model_output(output, (work_item_id,), authority_invalid_ids={work_item_id})
