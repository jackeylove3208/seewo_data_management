from pathlib import Path

import pytest

from app.ai.skills.registry import SkillNotFound, SkillRegistry, UnsafeSkillError


def test_analysis_skill_loads_a_pinned_read_only_contract() -> None:
    skill = SkillRegistry().load("analyze-data-difference", "1.0.0")

    assert skill.name == "analyze-data-difference"
    assert skill.output_schema == "CauseAnalysis"
    assert set(skill.allowed_tools) == {
        "difference_context",
        "candidate_search",
        "mapping_rules",
    }
    assert "write" not in skill.instructions.casefold()


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
