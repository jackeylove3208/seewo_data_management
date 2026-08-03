import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.ai.agent_analysis_service import SingleAttemptModelProvider
from app.ai.agent_prompting import (
    build_agent_request,
    build_json_repair_request,
    extract_model_result,
)
from app.ai.providers.base import LLMResponse, ModelProviderError
from app.ai.skills.registry import SkillRegistry
from app.api_connectors.contracts import OrganizationInspection, OrganizationUnitNode

_SKILL_VERSION: Literal["2.0.0"] = "2.0.0"
_TEACHER_UNIT_NAMES = frozenset(
    {"教职工", "教师", "老师", "员工", "行政人员", "教职员工"}
)
_STUDENT_UNIT_NAMES = frozenset({"学生", "学员"})


class ClassificationAttemptEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    model: str
    request_id: str | None = None
    outcome: Literal["accepted", "rejected"]


class OrganizationClassificationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    department_entity_kinds: dict[str, Literal["teacher", "student"]]
    person_membership_entity_kinds: dict[str, Literal["teacher", "student"]]
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    skill_version: Literal["2.0.0"] = _SKILL_VERSION
    attempts: tuple[ClassificationAttemptEvidence, ...]


class _MembershipClassificationItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    membership_key: str = Field(min_length=1, max_length=4096)
    entity_kind: Literal["teacher", "student"]


class _ClassificationResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    classifications: tuple[_MembershipClassificationItem, ...] = Field(min_length=1)


class OrganizationClassificationError(ValueError):
    def __init__(
        self,
        safe_code: str,
        *,
        issue_paths: tuple[tuple[str, ...], ...] = (),
    ) -> None:
        self.safe_code = safe_code
        self.issue_paths = issue_paths
        super().__init__(safe_code)


class _RepairableClassificationError(ValueError):
    def __init__(self, safe_code: str) -> None:
        self.safe_code = safe_code
        super().__init__(safe_code)


class DingTalkOrganizationUnitClassifier:
    def __init__(
        self,
        provider: SingleAttemptModelProvider,
        *,
        skills: SkillRegistry | None = None,
        max_attempts: int = 3,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("classification attempts must be positive")
        self._provider = provider
        self._skills = skills or SkillRegistry()
        self._max_attempts = max_attempts

    async def classify(
        self,
        inspection: OrganizationInspection,
    ) -> OrganizationClassificationResult:
        department_kinds = _explicit_department_kinds(inspection.departments)
        membership_kinds, unresolved = _resolve_memberships(
            inspection,
            department_kinds,
        )
        model_input: dict[str, object] = {
            "departments": [
                node.model_dump(mode="json")
                for node in sorted(
                    inspection.departments,
                    key=lambda item: item.department_id,
                )
            ],
            "unresolved_memberships": [
                {
                    "membership_key": key,
                    "department_ids": key.split("|"),
                }
                for key in sorted(unresolved)
            ],
        }
        attempts: list[ClassificationAttemptEvidence] = []
        if unresolved:
            model_decisions, attempts = await self._classify_unresolved(
                model_input,
                unresolved,
            )
            membership_kinds.update(model_decisions)
        output = {
            "department_entity_kinds": department_kinds,
            "person_membership_entity_kinds": membership_kinds,
        }
        return OrganizationClassificationResult(
            **output,
            input_hash=_hash_json(model_input),
            output_hash=_hash_json(output),
            attempts=tuple(attempts),
        )

    async def _classify_unresolved(
        self,
        model_input: dict[str, object],
        unresolved: frozenset[str],
    ) -> tuple[
        dict[str, Literal["teacher", "student"]],
        list[ClassificationAttemptEvidence],
    ]:
        skill = self._skills.load(
            "classify-dingtalk-organization-units",
            _SKILL_VERSION,
        )
        request = build_agent_request(skill, model_input, _ClassificationResponse)
        attempts: list[ClassificationAttemptEvidence] = []
        last_error: Exception | None = None
        for attempt_number in range(1, self._max_attempts + 1):
            response: LLMResponse | None = None
            try:
                response = await self._provider.complete_json_once(request)
                parsed = _ClassificationResponse.model_validate(
                    extract_model_result(response.output)
                )
                decisions = _validate_model_decisions(parsed, unresolved)
            except ModelProviderError:
                raise
            except (ValidationError, _RepairableClassificationError) as error:
                last_error = error
                if response is not None:
                    attempts.append(_attempt(response, "rejected"))
                if attempt_number == self._max_attempts:
                    raise OrganizationClassificationError(
                        "connector_entity_classification_invalid"
                    ) from error
                request = build_json_repair_request(
                    request,
                    response.output if response is not None else None,
                    error,
                )
                continue
            attempts.append(_attempt(response, "accepted"))
            return decisions, attempts
        assert last_error is not None
        raise OrganizationClassificationError(
            "connector_entity_classification_invalid"
        ) from last_error


def canonical_membership_key(memberships: tuple[str, ...]) -> str:
    return "|".join(sorted(set(memberships)))


def _explicit_department_kinds(
    departments: tuple[OrganizationUnitNode, ...],
) -> dict[str, Literal["teacher", "student"]]:
    nodes = {node.department_id: node for node in departments}
    expanded: dict[str, Literal["teacher", "student"]] = {}
    for node in sorted(departments, key=lambda item: len(item.path)):
        if node.parent_id is not None and node.parent_id not in nodes:
            raise OrganizationClassificationError(
                "connector_entity_classification_invalid"
            )
        inherited = expanded.get(node.parent_id or "")
        explicit = _explicit_name_kind(node.name)
        if inherited is not None:
            if explicit is not None and explicit != inherited:
                continue
            expanded[node.department_id] = inherited
        elif explicit is not None:
            expanded[node.department_id] = explicit
    return expanded


def _explicit_name_kind(
    name: str,
) -> Literal["teacher", "student"] | None:
    normalized = "".join(name.split()).casefold()
    if normalized in _TEACHER_UNIT_NAMES:
        return "teacher"
    if normalized in _STUDENT_UNIT_NAMES:
        return "student"
    return None


def _resolve_memberships(
    inspection: OrganizationInspection,
    department_kinds: dict[str, Literal["teacher", "student"]],
) -> tuple[
    dict[str, Literal["teacher", "student"]],
    frozenset[str],
]:
    known_ids = {node.department_id for node in inspection.departments}
    if not inspection.personnel_department_ids <= known_ids:
        raise OrganizationClassificationError(
            "connector_entity_classification_invalid"
        )
    resolved: dict[str, Literal["teacher", "student"]] = {}
    unresolved: set[str] = set()
    for memberships in inspection.personnel_memberships:
        key = canonical_membership_key(memberships)
        if not key or not set(memberships) <= known_ids:
            raise OrganizationClassificationError(
                "connector_entity_classification_invalid"
            )
        kinds = {
            department_kinds[department_id]
            for department_id in memberships
            if department_id in department_kinds
        }
        if len(kinds) == 1:
            resolved[key] = kinds.pop()
        else:
            unresolved.add(key)
    return resolved, frozenset(unresolved)


def _validate_model_decisions(
    response: _ClassificationResponse,
    expected: frozenset[str],
) -> dict[str, Literal["teacher", "student"]]:
    decisions: dict[str, Literal["teacher", "student"]] = {}
    for item in response.classifications:
        if item.membership_key in decisions:
            raise _RepairableClassificationError(
                "connector_entity_classification_invalid"
            )
        decisions[item.membership_key] = item.entity_kind
    if set(decisions) != expected:
        raise _RepairableClassificationError(
            "connector_entity_classification_invalid"
        )
    return decisions


def _attempt(
    response: LLMResponse,
    outcome: Literal["accepted", "rejected"],
) -> ClassificationAttemptEvidence:
    return ClassificationAttemptEvidence(
        provider=response.provider,
        model=response.model,
        request_id=response.request_id,
        outcome=outcome,
    )


def _hash_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
