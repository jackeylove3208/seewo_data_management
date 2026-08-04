"""Typed model executors for graph ingestion and reconciliation actions."""

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agent_analysis import operation_is_allowed
from app.ai.agent_batching import MAX_MODEL_ANALYSIS_BATCH_SIZE
from app.ai.graph_subagents import (
    GraphSkillInvocation,
    GraphSkillModelRunner,
    GraphSkillRunResult,
)
from app.ai.skills.contracts import (
    AgentFinding,
    AgentFindingBatch,
    AnalysisTemplateOutput,
    AnalysisTemplateProfile,
    GovernanceSolution,
    GovernanceSolutionBatch,
    IdentityWorkItem,
    NormalizedOrganizationBatch,
    SourceInspectionResult,
)
from app.governance.agent_governance import (
    AgentFindingInput,
    AgentOperation,
    AgentRiskPolicy,
)
from app.ingestion.agent_contract import AgentContractMapper
from app.repositories.agent_analysis import AgentAnalysisRepository
from app.schemas.agent_ingestion import (
    AgentContractRecord,
    AgentEntityKind,
    AgentInputMark,
    AgentSourceRole,
)
from app.schemas.agent_reconciliation import (
    AgentFindingPayload,
    AgentSolutionPayload,
)

_ACTIONABLE_KINDS = frozenset(
    {
        "target_extra",
        "target_duplicate",
        "target_missing",
        "field_difference",
        "authority_invalid",
        "identity_conflict",
    }
)


@dataclass(frozen=True)
class GraphAnalysisActionResult:
    payloads: tuple[AgentFindingPayload, ...]
    reconciliation_invocation_id: UUID
    solution_invocation_id: UUID


@dataclass(frozen=True)
class GraphAnalysisTemplateResult:
    action_result: GraphAnalysisActionResult
    template: AnalysisTemplateOutput


@dataclass(frozen=True)
class AnalysisTemplateContext:
    profile: AnalysisTemplateProfile
    profile_hash: str
    work_items: tuple[IdentityWorkItem, ...]


class AnalysisOutputValidationError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.repair_feedback = ({"path": "$", "code": code},)


class AnalysisTemplateValidationError(AnalysisOutputValidationError):
    pass


def _analysis_error(code: str, path: str) -> AnalysisOutputValidationError:
    error = AnalysisOutputValidationError(code)
    error.repair_feedback = ({"path": path, "code": code},)
    return error


def build_analysis_template_context(
    work_items: Sequence[IdentityWorkItem],
) -> AnalysisTemplateContext | None:
    context, fallback = partition_analysis_template_work(work_items)
    if fallback:
        return None
    return context


def partition_analysis_template_work(
    work_items: Sequence[IdentityWorkItem],
) -> tuple[AnalysisTemplateContext | None, tuple[IdentityWorkItem, ...]]:
    grouped: dict[str, tuple[AnalysisTemplateProfile, list[IdentityWorkItem]]] = {}
    ineligible: list[IdentityWorkItem] = []
    for item in work_items:
        profile = _analysis_template_profile(item)
        if profile is None:
            ineligible.append(item)
            continue
        profile_key = json.dumps(
            profile.model_dump(mode="json"),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        grouped.setdefault(profile_key, (profile, []))[1].append(item)
    if not grouped:
        return None, tuple(ineligible)
    profile_key, (profile, eligible) = min(
        grouped.items(),
        key=lambda entry: (-len(entry[1][1]), entry[0]),
    )
    fallback_ids = {
        item.work_item_id
        for key, (_candidate, items) in grouped.items()
        if key != profile_key
        for item in items
    }
    fallback_ids.update(item.work_item_id for item in ineligible)
    fallback = tuple(
        item for item in work_items if item.work_item_id in fallback_ids
    )
    profile_payload = profile.model_dump(mode="json")
    encoded = json.dumps(
        profile_payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return (
        AnalysisTemplateContext(
            profile=profile,
            profile_hash=f"sha256:{hashlib.sha256(encoded).hexdigest()}",
            work_items=tuple(eligible),
        ),
        fallback,
    )


def instantiate_analysis_template(
    context: AnalysisTemplateContext,
    template: AnalysisTemplateOutput,
    *,
    allowed_evidence_refs: frozenset[str],
) -> tuple[AgentFindingPayload, ...]:
    if template.profile_hash != context.profile_hash:
        raise AnalysisTemplateValidationError("analysis_template_profile_hash_mismatch")
    if not operation_is_allowed(
        context.profile.disposition,
        template.proposed_operation,
    ):
        raise AnalysisTemplateValidationError("analysis_template_operation_invalid")
    _reject_representative_values(context.work_items[0], template)
    expected_risk = _template_risk(
        profile=context.profile,
        operation=template.proposed_operation,
    )
    if template.risk != expected_risk:
        raise AnalysisTemplateValidationError("analysis_template_risk_invalid")

    payloads: list[AgentFindingPayload] = []
    for item in context.work_items:
        evidence_refs = item.paired_evidence.evidence_refs
        if not evidence_refs or not set(evidence_refs).issubset(
            allowed_evidence_refs
        ):
            raise AnalysisTemplateValidationError(
                "analysis_template_evidence_outside_manifest"
            )
        payloads.append(
            AgentFindingPayload(
                work_item_id=item.work_item_id,
                kind=context.profile.disposition,
                category_zh=template.category_zh,
                analysis_zh=template.analysis_zh,
                evidence_refs=evidence_refs,
                solutions=(
                    AgentSolutionPayload(
                        operation=template.proposed_operation,
                        risk=expected_risk,
                        solution_zh=template.solution_zh,
                        recommended=True,
                        dependency_finding_ids=(),
                    ),
                ),
            )
        )
    return tuple(payloads)


def _analysis_template_profile(
    item: IdentityWorkItem,
) -> AnalysisTemplateProfile | None:
    evidence = item.paired_evidence
    if (
        evidence.work_item_id != str(item.work_item_id)
        or evidence.entity_kind != item.entity_kind
        or evidence.persisted_kind not in {"target_extra", "target_missing"}
        or evidence.field_differences
        or evidence.candidate_conflicts
        or evidence.authority_claim is not None
        or evidence.evidence_ref not in item.candidate_evidence_refs
    ):
        return None

    if evidence.persisted_kind == "target_extra":
        if (
            evidence.target_record is None
            or evidence.authority_record is not None
            or evidence.identity_key_hits
            or evidence.allowed_candidates
            or evidence.allowed_operations != ("delete",)
        ):
            return None
        record = evidence.target_record
        identity_state = "no_candidate"
    else:
        if (
            evidence.authority_record is None
            or evidence.target_record is not None
            or set(evidence.allowed_operations) != {"create", "retain"}
        ):
            return None
        authority_ref = evidence.authority_record.get("input_ref")
        if not isinstance(authority_ref, str) or any(
            candidate != authority_ref for candidate in evidence.allowed_candidates
        ):
            return None
        if any(
            hit.authority_ref != authority_ref for hit in evidence.identity_key_hits
        ):
            return None
        record = evidence.authority_record
        identity_state = "unclaimed_authority"

    record_fields = tuple(
        field
        for field in ("category", "name", "number", "class_name", "phone", "email")
        if ("phone_token" if field == "phone" else field) in record
    )
    return AnalysisTemplateProfile(
        entity_kind=item.entity_kind,
        disposition=evidence.persisted_kind,
        allowed_operations=evidence.allowed_operations,
        authority_record_present=evidence.authority_record is not None,
        target_record_present=evidence.target_record is not None,
        record_fields=record_fields,
        identity_state=identity_state,
        risk_policy_version=AgentRiskPolicy.version,
        template_policy_version="analysis-template-v1",
    )


def _template_risk(
    *,
    profile: AnalysisTemplateProfile,
    operation: str,
) -> str:
    decision = AgentRiskPolicy().assess(
        AgentFindingInput(
            finding_id=UUID(int=0),
            work_item_id=UUID(int=0),
            entity_kind=profile.entity_kind,
            kind=profile.disposition,
            operation=AgentOperation(operation),
            changed_fields=frozenset(),
            before={} if profile.target_record_present else None,
            after={} if profile.authority_record_present else None,
            target_source_identifier=None,
            dependencies=frozenset(),
            analysis_terminal=True,
            target_version="analysis-template-v1",
        )
    )
    return decision.risk


def _reject_representative_values(
    representative: IdentityWorkItem,
    template: AnalysisTemplateOutput,
) -> None:
    output_text = "\n".join(
        (template.category_zh, template.analysis_zh, template.solution_zh)
    )
    sensitive_values = {
        str(representative.work_item_id),
        representative.target_locator,
        *representative.candidate_evidence_refs,
        *representative.paired_evidence.evidence_refs,
        *representative.paired_evidence.allowed_candidates,
    }
    for record in (
        representative.paired_evidence.authority_record,
        representative.paired_evidence.target_record,
    ):
        if record is None:
            continue
        for field in (
            "input_ref",
            "locator",
            "name",
            "number",
            "class_name",
            "phone_token",
            "email",
        ):
            value = record.get(field)
            if isinstance(value, str) and value:
                sensitive_values.add(value)
    if any(value in output_text for value in sensitive_values if value):
        raise AnalysisTemplateValidationError(
            "analysis_template_contains_representative_value"
        )


class GraphIngestionAnalysisExecutors:
    """Invoke the three milestone-one Skills without legacy Handler fallback."""

    def __init__(self, runner: GraphSkillModelRunner) -> None:
        self._runner = runner

    async def inspect_sources(
        self,
        invocation: GraphSkillInvocation,
    ) -> GraphSkillRunResult:
        if invocation.graph_node != "inspect_sources":
            raise ValueError("source inspection requires inspect_sources graph node")
        return await self._runner.run(
            invocation.model_copy(
                update={
                    "skill_name": "inspect-external-data-source",
                    "skill_version": "1.0.0",
                }
            ),
            result_validator=_require_source_inspection,
        )

    async def normalize_input_batch(
        self,
        invocation: GraphSkillInvocation,
        *,
        expected_locators: Sequence[str],
        assert_known_phone_tokens: Callable[[set[str]], None] | None = None,
    ) -> GraphSkillRunResult:
        if invocation.graph_node != "normalize_input_batches":
            raise ValueError(
                "input normalization requires normalize_input_batches graph node"
            )
        if len(expected_locators) > 50:
            raise ValueError("one normalization invocation cannot exceed fifty rows")

        def validate(output: BaseModel) -> BaseModel:
            if not isinstance(output, NormalizedOrganizationBatch):
                raise ValueError("normalization Skill returned another schema")
            validated = validate_normalized_output(expected_locators, output)
            phone_tokens = {
                record.phone_token
                for record in validated.records
                if record.phone_token is not None
            }
            if phone_tokens:
                if assert_known_phone_tokens is None:
                    raise ValueError("phone-token membership validator is required")
                assert_known_phone_tokens(phone_tokens)
            return validated

        return await self._runner.run(
            invocation.model_copy(
                update={
                    "skill_name": "normalize-organization-data-batch",
                    "skill_version": "1.0.0",
                }
            ),
            result_validator=validate,
        )

    async def map_database_schema(
        self,
        invocation: GraphSkillInvocation,
        *,
        result_validator: Callable[[BaseModel], BaseModel],
    ) -> GraphSkillRunResult:
        if invocation.graph_node != "normalize_input_batches":
            raise ValueError(
                "database schema mapping requires normalize_input_batches graph node"
            )
        return await self._runner.run(
            invocation.model_copy(
                update={
                    "skill_name": "understand-organization-database-schema",
                    "skill_version": "1.0.0",
                }
            ),
            result_validator=result_validator,
        )

    async def analyze_actionable_batch(
        self,
        invocation: GraphSkillInvocation,
        *,
        expected_work_item_kinds: Mapping[UUID, str],
        allowed_evidence_refs: frozenset[str],
    ) -> GraphAnalysisActionResult:
        if invocation.graph_node != "analyze_actionable_batches":
            raise ValueError(
                "reconciliation requires analyze_actionable_batches graph node"
            )
        if not 1 <= len(expected_work_item_kinds) <= MAX_MODEL_ANALYSIS_BATCH_SIZE:
            raise ValueError("one analysis invocation requires one to ten work items")

        def validate_findings(output: BaseModel) -> BaseModel:
            if not isinstance(output, AgentFindingBatch):
                raise ValueError("reconciliation Skill returned another schema")
            compile_combined_analysis_payloads(
                expected_work_item_kinds=expected_work_item_kinds,
                allowed_evidence_refs=allowed_evidence_refs,
                findings=output,
            )
            return output

        reconciliation = await self._runner.run(
            invocation.model_copy(
                update={
                    "skill_name": "reconcile-entity-batch",
                    "skill_version": "1.0.0",
                }
            ),
            result_validator=validate_findings,
        )
        findings = reconciliation.output
        if not isinstance(findings, AgentFindingBatch):
            raise RuntimeError("validated reconciliation output changed type")

        return GraphAnalysisActionResult(
            payloads=compile_combined_analysis_payloads(
                expected_work_item_kinds=expected_work_item_kinds,
                allowed_evidence_refs=allowed_evidence_refs,
                findings=findings,
            ),
            reconciliation_invocation_id=reconciliation.invocation_id,
            solution_invocation_id=reconciliation.invocation_id,
        )

    async def derive_actionable_template(
        self,
        invocation: GraphSkillInvocation,
        *,
        template_context: AnalysisTemplateContext,
        allowed_evidence_refs: frozenset[str],
    ) -> GraphAnalysisTemplateResult:
        if invocation.graph_node != "analyze_actionable_batches":
            raise ValueError(
                "analysis template requires analyze_actionable_batches graph node"
            )

        def validate_template(output: BaseModel) -> BaseModel:
            if not isinstance(output, AnalysisTemplateOutput):
                raise AnalysisTemplateValidationError(
                    "analysis_template_schema_invalid"
                )
            instantiate_analysis_template(
                template_context,
                output,
                allowed_evidence_refs=allowed_evidence_refs,
            )
            return output

        result = await self._runner.run(
            invocation.model_copy(
                update={
                    "skill_name": "derive-reconciliation-analysis-template",
                    "skill_version": "1.0.0",
                }
            ),
            result_validator=validate_template,
        )
        template = result.output
        if not isinstance(template, AnalysisTemplateOutput):
            raise RuntimeError("validated analysis template changed type")
        return GraphAnalysisTemplateResult(
            action_result=GraphAnalysisActionResult(
                payloads=instantiate_analysis_template(
                    template_context,
                    template,
                    allowed_evidence_refs=allowed_evidence_refs,
                ),
                reconciliation_invocation_id=result.invocation_id,
                solution_invocation_id=result.invocation_id,
            ),
            template=template,
        )


class GraphAnalysisResultWriter:
    """Persist only schema-validated graph model results and server-owned marks."""

    def __init__(self, session: AsyncSession) -> None:
        self._repository = AgentAnalysisRepository(session)
        self._mapper = AgentContractMapper()

    async def persist_normalized_batch(
        self,
        *,
        task_id: UUID,
        run_id: UUID,
        snapshot_id: UUID,
        tenant_id: str,
        source_role: AgentSourceRole,
        output: NormalizedOrganizationBatch,
        resolve_phone_token: Callable[[str | None], str | None] | None = None,
    ) -> tuple[AgentContractRecord, ...]:
        records: list[AgentContractRecord] = []
        for normalized in output.records:
            if normalized.entity_kind is None:
                raise ValueError(
                    "unrecognized entity category requires abnormal-input reporting"
                )
            row_number = _csv_row_number(normalized.locator)
            phone = normalized.phone_token
            if phone is not None:
                if resolve_phone_token is None:
                    raise ValueError("phone-token resolver is required for persistence")
                phone = resolve_phone_token(phone)
            records.append(
                AgentContractRecord(
                    task_id=task_id,
                    run_id=run_id,
                    snapshot_id=snapshot_id,
                    tenant_id=tenant_id,
                    source_role=source_role,
                    stable_locator=normalized.locator,
                    stable_order=row_number - 1,
                    entity_kind=AgentEntityKind(normalized.entity_kind),
                    category=normalized.category,
                    name=normalized.name,
                    number=normalized.number,
                    class_name=normalized.class_name,
                    phone=phone,
                    email=normalized.email,
                    raw_row_number=row_number,
                )
            )
        persisted = await self._repository.persist_inputs(tuple(records))
        marks: list[AgentInputMark] = []
        for normalized, record, saved in zip(
            output.records,
            records,
            persisted,
            strict=True,
        ):
            mark = self._mapper.validation_mark(record)
            if mark is None and normalized.invalid:
                mark = AgentInputMark(
                    input_record_id=saved.id,
                    reason_code="model_marked_input_invalid",
                    affected_fields=normalized.exclusion_codes,
                    inclusion_state=(
                        "excluded"
                        if source_role is AgentSourceRole.AUTHORITATIVE
                        else "anomaly"
                    ),
                    report_disposition=(
                        "mandatory_ai_anomaly"
                        if source_role is AgentSourceRole.AUTHORITATIVE
                        else "target_extra"
                    ),
                    safe_evidence={
                        "code": "model_marked_input_invalid",
                        "entity_kind": record.entity_kind.value,
                        "row_number": record.raw_row_number,
                        "source_role": source_role.value,
                    },
                )
            elif mark is not None:
                mark = mark.model_copy(update={"input_record_id": saved.id})
            if mark is not None:
                marks.append(mark)
        await self._repository.persist_marks(tuple(marks))
        return tuple(records)


def validate_normalized_output(
    expected_locators: Sequence[str],
    output: NormalizedOrganizationBatch,
) -> NormalizedOrganizationBatch:
    actual = tuple(record.locator for record in output.records)
    if (
        len(set(actual)) != len(actual)
        or actual != tuple(expected_locators)
        or len(actual) > 50
    ):
        raise ValueError("normalized output must exactly cover manifest rows in order")
    return output


def compile_analysis_payloads(
    *,
    expected_work_item_kinds: Mapping[UUID, str],
    allowed_evidence_refs: frozenset[str],
    findings: AgentFindingBatch,
    solutions: GovernanceSolutionBatch,
) -> tuple[AgentFindingPayload, ...]:
    if not expected_work_item_kinds or any(
        kind not in _ACTIONABLE_KINDS for kind in expected_work_item_kinds.values()
    ):
        raise _analysis_error("analysis_input_not_actionable", "work_items")
    finding_by_id = _validate_finding_membership(
        expected_work_item_kinds=expected_work_item_kinds,
        allowed_evidence_refs=allowed_evidence_refs,
        findings=findings,
    )

    solution_by_id = {solution.finding_id: solution for solution in solutions.solutions}
    if len(solution_by_id) != len(solutions.solutions):
        raise _analysis_error("analysis_solution_duplicate", "solutions")
    missing_solution_ids = set(finding_by_id) - set(solution_by_id)
    if missing_solution_ids:
        raise _analysis_error("analysis_solution_missing", "solutions")
    if set(solution_by_id) - set(finding_by_id):
        raise _analysis_error("analysis_solution_unexpected", "solutions")

    payloads: list[AgentFindingPayload] = []
    for index, finding in enumerate(findings.findings):
        solution = solution_by_id[finding.finding_id]
        if solution.operation != finding.proposed_operation:
            raise _analysis_error(
                "analysis_solution_operation_mismatch",
                f"solutions[{index}].operation",
            )
        if not operation_is_allowed(finding.disposition, solution.operation):
            raise _analysis_error(
                "analysis_operation_invalid",
                f"solutions[{index}].operation",
            )
        payloads.append(
            AgentFindingPayload(
                work_item_id=finding.work_item_id,
                kind=finding.disposition,
                category_zh=finding.category_zh,
                analysis_zh=finding.analysis_zh,
                evidence_refs=finding.evidence_refs,
                solutions=(
                    AgentSolutionPayload(
                        operation=solution.operation,
                        risk=solution.risk,
                        solution_zh=solution.solution_zh,
                        recommended=True,
                        dependency_finding_ids=solution.dependency_finding_ids,
                    ),
                ),
            )
        )
    return tuple(payloads)


def compile_combined_analysis_payloads(
    *,
    expected_work_item_kinds: Mapping[UUID, str],
    allowed_evidence_refs: frozenset[str],
    findings: AgentFindingBatch,
) -> tuple[AgentFindingPayload, ...]:
    return compile_analysis_payloads(
        expected_work_item_kinds=expected_work_item_kinds,
        allowed_evidence_refs=allowed_evidence_refs,
        findings=findings,
        solutions=GovernanceSolutionBatch(
            schema_version=findings.schema_version,
            solutions=tuple(
                GovernanceSolution(
                    finding_id=finding.finding_id,
                    solution_zh=finding.solution_zh,
                    operation=finding.proposed_operation,
                    risk=finding.risk,
                    dependency_finding_ids=finding.dependency_finding_ids,
                )
                for finding in findings.findings
            ),
        ),
    )


def _validate_finding_membership(
    *,
    expected_work_item_kinds: Mapping[UUID, str],
    allowed_evidence_refs: frozenset[str],
    findings: AgentFindingBatch,
) -> Mapping[UUID, AgentFinding]:
    finding_by_id = {finding.finding_id: finding for finding in findings.findings}
    work_item_ids = tuple(finding.work_item_id for finding in findings.findings)
    if len(finding_by_id) != len(findings.findings):
        raise _analysis_error("analysis_finding_id_duplicate", "findings")
    if len(set(work_item_ids)) != len(work_item_ids):
        raise _analysis_error("analysis_work_item_duplicate", "findings")
    missing_ids = set(expected_work_item_kinds) - set(work_item_ids)
    if missing_ids:
        raise _analysis_error("analysis_work_item_missing", "findings")
    if set(work_item_ids) - set(expected_work_item_kinds):
        raise _analysis_error("analysis_work_item_unexpected", "findings")
    for index, finding in enumerate(findings.findings):
        if finding.disposition != expected_work_item_kinds[finding.work_item_id]:
            raise _analysis_error(
                "analysis_disposition_mismatch",
                f"findings[{index}].disposition",
            )
        if not finding.evidence_refs:
            raise _analysis_error(
                "analysis_evidence_missing",
                f"findings[{index}].evidence_refs",
            )
        if not set(finding.evidence_refs).issubset(allowed_evidence_refs):
            raise _analysis_error(
                "analysis_evidence_outside_manifest",
                f"findings[{index}].evidence_refs",
            )
    return finding_by_id


def _require_source_inspection(output: BaseModel) -> BaseModel:
    if not isinstance(output, SourceInspectionResult):
        raise ValueError("source inspection Skill returned another schema")
    return output


def _csv_row_number(locator: str) -> int:
    prefix, separator, value = locator.partition(":")
    if prefix != "csv" or separator != ":" or not value.isdecimal():
        raise ValueError("normalized CSV locator is invalid")
    row_number = int(value)
    if row_number < 2:
        raise ValueError("normalized CSV locator must reference a data row")
    return row_number
