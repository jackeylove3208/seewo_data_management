from pathlib import Path

import pytest

from app.ai.skills.registry import SkillNotFound, SkillRegistry, UnsafeSkillError


def test_analysis_skill_loads_a_pinned_read_only_contract() -> None:
    skill = SkillRegistry().load("analyze-data-difference", "1.0.0")

    assert skill.name == "analyze-data-difference"
    assert skill.output_schema == "CauseAnalysisV3"
    assert set(skill.allowed_tools) == {
        "difference_context",
        "candidate_search",
        "mapping_rules",
    }
    assert "write" not in skill.instructions.casefold()
    assert "简体中文" in skill.instructions
    assert "不得请求或调用任何目标系统写操作" in skill.instructions


def test_unknown_skill_version_fails_closed() -> None:
    with pytest.raises(SkillNotFound, match="analyze-data-difference@9.9.9"):
        SkillRegistry().load("analyze-data-difference", "9.9.9")


def test_registry_rejects_a_skill_with_non_read_only_tool(tmp_path: Path) -> None:
    skill_path = tmp_path / "unsafe" / "SKILL.md"
    skill_path.parent.mkdir()
    skill_path.write_text(
        """---
name: unsafe
version: 1.0.0
allowed_tools: [apply_target_update]
output_schema: CauseAnalysis
---
Unsafe skill.
""",
        encoding="utf-8",
    )

    with pytest.raises(UnsafeSkillError, match="unsafe"):
        SkillRegistry(root=tmp_path).load("unsafe", "1.0.0")


def test_agent_skill_loads_a_pinned_phase_contract() -> None:
    skill = SkillRegistry().load("reconcile-entity-batch", "1.0.0")

    assert skill.phase == "analyze_batches"
    assert set(skill.allowed_tools) == {
        "read_work_item",
        "read_paired_record_evidence",
        "query_identity_postings",
        "read_claim_state",
        "submit_finding_batch",
    }
    assert skill.output_schema == "AgentFindingBatch"
    assert skill.input_schema == "ReconcileEntityBatchInput"

    validated_input = SkillRegistry().validate_input(
        skill,
        {
            "task_id": "c519451d-2b27-4ce2-a76c-e0ca9a8e946f",
            "run_id": "59866fb0-40d4-4ae8-b5f0-4543fdd1b567",
            "phase": "analyze_batches",
            "evidence_refs": ["work-item:1"],
            "work_items": [],
        },
    )
    assert validated_input.phase == "analyze_batches"
    validated_output = SkillRegistry().validate_output(
        skill,
        {"schema_version": "agent-contract-v1", "findings": []},
    )
    assert validated_output.schema_version == "agent-contract-v1"

    with pytest.raises(UnsafeSkillError, match="reconcile-entity-batch"):
        SkillRegistry().validate_input(
            skill,
            {
                "task_id": "c519451d-2b27-4ce2-a76c-e0ca9a8e946f",
                "run_id": "59866fb0-40d4-4ae8-b5f0-4543fdd1b567",
                "phase": "execute_and_verify",
                "work_items": [],
            },
        )


def test_agent_skill_contract_rejects_unknown_fields_and_schema_names(tmp_path: Path) -> None:
    skill = SkillRegistry().load("reconcile-entity-batch", "1.0.0")
    with pytest.raises(ValueError, match="Extra inputs"):
        SkillRegistry().validate_output(
            skill,
            {
                "schema_version": "agent-contract-v1",
                "findings": [],
                "untrusted": "ignored?",
            },
        )
    with pytest.raises(ValueError, match="category_zh"):
        SkillRegistry().validate_output(
            skill,
            {
                "schema_version": "agent-contract-v1",
                "findings": [
                    {
                        "finding_id": "c519451d-2b27-4ce2-a76c-e0ca9a8e946f",
                        "work_item_id": "59866fb0-40d4-4ae8-b5f0-4543fdd1b567",
                        "disposition": "target_extra",
                        "analysis_zh": "缺少权威对应记录",
                        "proposed_operation": "delete",
                        "evidence_refs": ["row:1"],
                    }
                ],
            },
        )

    skill_path = tmp_path / "unknown-schema" / "SKILL.md"
    skill_path.parent.mkdir()
    skill_path.write_text(
        """---
name: unknown-schema
version: 1.0.0
phase: analyze_batches
allowed_tools: []
input_schema: DoesNotExist
output_schema: AlsoMissing
---
Unknown schema skill.
""",
        encoding="utf-8",
    )
    with pytest.raises(UnsafeSkillError, match="unknown-schema"):
        SkillRegistry(root=tmp_path).load("unknown-schema", "1.0.0")


def test_registry_rejects_agent_tool_from_another_phase(tmp_path: Path) -> None:
    skill_path = tmp_path / "unsafe-agent" / "SKILL.md"
    skill_path.parent.mkdir()
    skill_path.write_text(
        """---
name: unsafe-agent
version: 1.0.0
phase: analyze_batches
allowed_tools: [execute_target_operation]
output_schema: AgentFindingBatch
---
Unsafe cross-phase skill.
""",
        encoding="utf-8",
    )

    with pytest.raises(UnsafeSkillError, match="unsafe-agent"):
        SkillRegistry(root=tmp_path).load("unsafe-agent", "1.0.0")
