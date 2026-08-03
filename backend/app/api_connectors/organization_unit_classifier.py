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
from app.api_connectors.contracts import OrganizationInspection

_SKILL_VERSION: Literal["1.0.0"] = "1.0.0"
_CLASSIFIED_KINDS = frozenset({"teacher", "student"})


class ClassificationAttemptEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: str
    model: str
    request_id: str | None = None
    outcome: Literal["accepted", "rejected"]


class OrganizationClassificationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    department_entity_kinds: dict[str, Literal["teacher", "student"]]
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    skill_version: Literal["1.0.0"] = _SKILL_VERSION
    attempts: tuple[ClassificationAttemptEvidence, ...]


class _ClassificationItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    department_id: str = Field(min_length=1, max_length=512)
    entity_kind: Literal["teacher", "student", "unknown"]


class _ClassificationResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    classifications: tuple[_ClassificationItem, ...] = Field(min_length=1)


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
    def __init__(
        self,
        safe_code: str,
        *,
        issue_paths: tuple[tuple[str, ...], ...] = (),
    ) -> None:
        self.safe_code = safe_code
        self.issue_paths = issue_paths
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
        model_input = {
            "departments": [
                node.model_dump(mode="json")
                for node in sorted(
                    inspection.departments,
                    key=lambda item: item.department_id,
                )
            ]
        }
        skill = self._skills.load(
            "classify-dingtalk-organization-units",
            _SKILL_VERSION,
        )
        request = build_agent_request(skill, model_input, _ClassificationResponse)
        attempts: list[ClassificationAttemptEvidence] = []
        last_error: Exception | None = None
        last_paths: tuple[tuple[str, ...], ...] = ()
        last_safe_code = "connector_entity_classification_invalid"
        for attempt_number in range(1, self._max_attempts + 1):
            response: LLMResponse | None = None
            try:
                response = await self._provider.complete_json_once(request)
                parsed = _ClassificationResponse.model_validate(
                    extract_model_result(response.output)
                )
                expanded = _validate_and_expand(parsed, inspection)
                _validate_memberships(expanded, inspection)
            except ModelProviderError:
                raise
            except (ValidationError, _RepairableClassificationError) as error:
                last_error = error
                if isinstance(error, _RepairableClassificationError):
                    last_safe_code = error.safe_code
                    last_paths = error.issue_paths
                if response is not None:
                    attempts.append(_attempt(response, "rejected"))
                if attempt_number == self._max_attempts:
                    raise OrganizationClassificationError(
                        last_safe_code,
                        issue_paths=last_paths,
                    ) from error
                request = build_json_repair_request(
                    request,
                    response.output if response is not None else None,
                    error,
                )
                continue
            attempts.append(_attempt(response, "accepted"))
            return OrganizationClassificationResult(
                department_entity_kinds=expanded,
                input_hash=_hash_json(model_input),
                output_hash=_hash_json(expanded),
                attempts=tuple(attempts),
            )
        assert last_error is not None
        raise OrganizationClassificationError(last_safe_code) from last_error


def _validate_and_expand(
    response: _ClassificationResponse,
    inspection: OrganizationInspection,
) -> dict[str, Literal["teacher", "student"]]:
    expected = {node.department_id for node in inspection.departments}
    labels: dict[str, Literal["teacher", "student", "unknown"]] = {}
    for item in response.classifications:
        if item.department_id in labels:
            raise _RepairableClassificationError(
                "connector_entity_classification_invalid"
            )
        labels[item.department_id] = item.entity_kind
    if set(labels) != expected:
        raise _RepairableClassificationError(
            "connector_entity_classification_invalid"
        )

    nodes = {node.department_id: node for node in inspection.departments}
    expanded: dict[str, Literal["teacher", "student"]] = {}
    for node in sorted(inspection.departments, key=lambda item: len(item.path)):
        if node.parent_id is not None and node.parent_id not in nodes:
            raise _RepairableClassificationError(
                "connector_entity_classification_invalid"
            )
        inherited = expanded.get(node.parent_id or "")
        explicit = labels[node.department_id]
        if inherited is not None:
            if explicit in _CLASSIFIED_KINDS and explicit != inherited:
                raise _RepairableClassificationError(
                    "connector_entity_classification_invalid",
                    issue_paths=(node.path,),
                )
            expanded[node.department_id] = inherited
        elif explicit == "teacher" or explicit == "student":
            expanded[node.department_id] = explicit

    unknown_paths = tuple(
        nodes[department_id].path
        for department_id in sorted(inspection.personnel_department_ids)
        if department_id in nodes and department_id not in expanded
    )
    if unknown_paths:
        raise _RepairableClassificationError(
            "connector_entity_classification_unknown",
            issue_paths=unknown_paths,
        )
    if not inspection.personnel_department_ids <= set(nodes):
        raise _RepairableClassificationError(
            "connector_entity_classification_invalid"
        )
    return expanded


def _validate_memberships(
    expanded: dict[str, Literal["teacher", "student"]],
    inspection: OrganizationInspection,
) -> None:
    for memberships in inspection.personnel_memberships:
        kinds = {expanded.get(department_id) for department_id in memberships}
        kinds.discard(None)
        if len(kinds) > 1:
            raise OrganizationClassificationError(
                "connector_entity_classification_ambiguous"
            )


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
