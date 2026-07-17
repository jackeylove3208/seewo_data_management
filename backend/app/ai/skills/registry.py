import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

READ_ONLY_TOOL_NAMES = frozenset(
    {"difference_context", "candidate_search", "mapping_rules", "execution_context"}
)


class SkillNotFound(LookupError):
    pass


class UnsafeSkillError(ValueError):
    pass


class SkillDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=64)
    allowed_tools: tuple[str, ...]
    output_schema: str = Field(min_length=1, max_length=128)
    instructions: str = Field(min_length=1)


class SkillRegistry:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path(__file__).resolve().parent

    def load(self, name: str, version: str) -> SkillDefinition:
        path = self.root / name / "SKILL.md"
        try:
            definition = _parse_skill(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise SkillNotFound(f"{name}@{version}") from error
        if definition.name != name or definition.version != version:
            raise SkillNotFound(f"{name}@{version}")
        if not set(definition.allowed_tools) <= READ_ONLY_TOOL_NAMES:
            raise UnsafeSkillError(name)
        return definition


def _parse_skill(content: str) -> SkillDefinition:
    if not content.startswith("---\n"):
        raise ValueError("Skill frontmatter is required")
    _opening, frontmatter, instructions = content.split("---\n", maxsplit=2)
    values: dict[str, object] = {}
    for line in frontmatter.splitlines():
        key, separator, value = line.partition(":")
        if not separator:
            raise ValueError("invalid Skill frontmatter")
        normalized_key = key.strip()
        normalized_value = value.strip()
        if normalized_key == "allowed_tools":
            values[normalized_key] = _parse_allowed_tools(normalized_value)
        else:
            values[normalized_key] = normalized_value
    values["instructions"] = instructions.strip()
    return SkillDefinition.model_validate(values)


def _parse_allowed_tools(value: str) -> tuple[str, ...]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        if not value.startswith("[") or not value.endswith("]"):
            raise ValueError("allowed_tools must be a list") from None
        parsed = [item.strip() for item in value[1:-1].split(",") if item.strip()]
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise ValueError("allowed_tools must contain strings")
    return tuple(parsed)
