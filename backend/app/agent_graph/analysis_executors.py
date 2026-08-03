"""Typed model executors for graph ingestion and reconciliation actions."""

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
    GovernanceSolution,
    GovernanceSolutionBatch,
    NormalizedOrganizationBatch,
    SourceInspectionResult,
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
        raise ValueError("analysis input must contain only actionable work items")
    finding_by_id = _validate_finding_membership(
        expected_work_item_kinds=expected_work_item_kinds,
        allowed_evidence_refs=allowed_evidence_refs,
        findings=findings,
    )

    solution_by_id = {solution.finding_id: solution for solution in solutions.solutions}
    if len(solution_by_id) != len(solutions.solutions) or set(solution_by_id) != set(
        finding_by_id
    ):
        raise ValueError("AI solutions must exactly cover analysis findings")

    payloads: list[AgentFindingPayload] = []
    for finding in findings.findings:
        solution = solution_by_id[finding.finding_id]
        if solution.operation != finding.proposed_operation:
            raise ValueError("AI solution operation conflicts with analysis finding")
        if not operation_is_allowed(finding.disposition, solution.operation):
            raise ValueError(
                "AI solution operation is incompatible with persisted work"
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
    if (
        len(finding_by_id) != len(findings.findings)
        or len(set(work_item_ids)) != len(work_item_ids)
        or set(work_item_ids) != set(expected_work_item_kinds)
    ):
        raise ValueError("analysis findings must exactly cover actionable work items")
    for finding in findings.findings:
        if finding.disposition != expected_work_item_kinds[finding.work_item_id]:
            raise ValueError("finding disposition does not match persisted work item")
        if not finding.evidence_refs or not set(finding.evidence_refs).issubset(
            allowed_evidence_refs
        ):
            raise ValueError("finding cites evidence outside evidence manifest")
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
