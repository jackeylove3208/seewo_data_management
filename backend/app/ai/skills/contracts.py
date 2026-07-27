from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.agent_graph.contracts import SupervisorContextV1, SupervisorDecisionV1
from app.agent_graph.evidence import PairedRecordEvidenceV1
from app.agent_runtime.state_machine import AgentPhase, AgentRunStatus

EntityKind = Literal["department", "student", "teacher"]
ConnectorKind = Literal["csv", "api", "database"]
SourceRole = Literal["authoritative", "target"]
RiskLevel = Literal["low", "medium", "high"]
OperationKind = Literal["create", "update", "delete", "retain", "skip"]
FixedContractField = Literal[
    "category",
    "name",
    "number",
    "class_name",
    "phone",
    "email",
]
FixedFieldNormalizer = Literal[
    "normalize_category",
    "trim_text",
    "trim_identifier",
    "normalize_phone",
    "normalize_email",
]


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AgentSkillInput(StrictContract):
    task_id: UUID
    run_id: UUID
    phase: AgentPhase
    evidence_refs: tuple[str, ...] = ()


class AgentSkillOutput(StrictContract):
    schema_version: Literal["agent-contract-v1"]


class RawRecord(StrictContract):
    locator: str = Field(min_length=1)
    fields: dict[str, str | None]


class IdentityWorkItem(StrictContract):
    work_item_id: UUID
    entity_kind: EntityKind
    target_locator: str
    candidate_evidence_refs: tuple[str, ...]
    paired_evidence: PairedRecordEvidenceV1


class FindingReference(StrictContract):
    finding_id: UUID
    evidence_refs: tuple[str, ...]
    proposed_operation: OperationKind


class OperationOutcome(StrictContract):
    operation_id: UUID
    status: Literal["succeeded", "failed", "blocked", "skipped"]
    verification_ref: str | None = None
    safe_error_code: str | None = None


class SupervisorPhaseInput(AgentSkillInput):
    current_status: AgentRunStatus
    available_commands: tuple[Literal["continue", "terminate"], ...]


class SupervisorPhaseDecision(AgentSkillOutput):
    next_phase: AgentPhase | None
    explanation: str = Field(min_length=1)


class SourceInspectionInput(AgentSkillInput):
    connector_kind: ConnectorKind
    connector_ref: str = Field(min_length=1)
    page_locator: str | None = None


class SourceInspectionResult(AgentSkillOutput):
    recognized: bool
    detected_fields: tuple[str, ...]
    entity_kinds: tuple[EntityKind, ...]
    safe_problem_codes: tuple[str, ...] = ()


class NormalizeOrganizationBatchInput(AgentSkillInput):
    source_role: SourceRole
    batch_resource_ids: tuple[str, ...] = Field(default=(), max_length=50)
    records: tuple[RawRecord, ...] = Field(max_length=50)


class NormalizedRecord(StrictContract):
    locator: str
    entity_kind: EntityKind | None
    category: str | None
    name: str | None
    number: str | None
    phone_token: str | None
    email: str | None
    class_name: str | None
    invalid: bool
    exclusion_codes: tuple[str, ...] = ()


class NormalizedOrganizationBatch(AgentSkillOutput):
    records: tuple[NormalizedRecord, ...] = Field(max_length=50)


class CsvColumnProfile(StrictContract):
    source_field_ref: str = Field(min_length=1, max_length=512)
    header: str = Field(min_length=1, max_length=255)
    inferred_type: Literal["text", "identifier", "phone", "email", "unknown"]
    empty_ratio: float = Field(ge=0, le=1)
    unique_ratio: float = Field(ge=0, le=1)
    candidate_contract_fields: tuple[FixedContractField, ...] = ()


class CsvSourceSchemaProfile(StrictContract):
    source_role: SourceRole
    columns: tuple[CsvColumnProfile, ...] = Field(min_length=1, max_length=256)


class CsvSchemaMappingInput(AgentSkillInput):
    sources: tuple[CsvSourceSchemaProfile, ...] = Field(min_length=2, max_length=2)


class CsvFieldMapping(StrictContract):
    source_field_ref: str = Field(min_length=1, max_length=512)
    contract_field: FixedContractField
    entity_kinds: tuple[EntityKind, ...] = Field(min_length=1, max_length=3)
    normalizer_id: FixedFieldNormalizer


class CsvSchemaMappingOutput(StrictContract):
    schema_version: Literal["fixed-six-field-mapping-v2"]
    authoritative_mappings: tuple[CsvFieldMapping, ...] = Field(max_length=6)
    target_mappings: tuple[CsvFieldMapping, ...] = Field(max_length=6)
    unresolved_required_fields: tuple[str, ...] = ()


class DatabaseColumnProfile(StrictContract):
    source_field_ref: str = Field(min_length=1, max_length=512)
    column_name: str = Field(min_length=1, max_length=255)
    inferred_type: Literal[
        "text",
        "identifier",
        "phone",
        "email",
        "unknown",
    ]
    nullable: bool
    candidate_contract_fields: tuple[FixedContractField, ...] = ()


class DatabaseSourceSchemaProfile(StrictContract):
    source_role: SourceRole
    connector_id: str = Field(min_length=1, max_length=255)
    dialect: Literal["mysql", "postgresql"]
    relation_ref: str = Field(min_length=1, max_length=512)
    stable_key_ref: str = Field(min_length=1, max_length=512)
    columns: tuple[DatabaseColumnProfile, ...] = Field(
        min_length=1,
        max_length=256,
    )


class DatabaseSchemaMappingInput(AgentSkillInput):
    sources: tuple[DatabaseSourceSchemaProfile, ...] = Field(
        min_length=2,
        max_length=2,
    )


class DatabaseFieldMapping(StrictContract):
    source_field_ref: str = Field(min_length=1, max_length=512)
    contract_field: FixedContractField
    entity_kinds: tuple[EntityKind, ...] = Field(min_length=1, max_length=3)
    normalizer_id: FixedFieldNormalizer


class DatabaseSchemaMappingOutput(StrictContract):
    schema_version: Literal["fixed-six-field-sql-mapping-v2"]
    authoritative_mappings: tuple[DatabaseFieldMapping, ...] = Field(max_length=6)
    target_mappings: tuple[DatabaseFieldMapping, ...] = Field(max_length=6)
    unresolved_required_fields: tuple[str, ...] = ()


class ReconcileEntityBatchInput(AgentSkillInput):
    work_items: tuple[IdentityWorkItem, ...] = Field(max_length=50)


class AgentFinding(StrictContract):
    finding_id: UUID
    work_item_id: UUID
    disposition: Literal[
        "target_extra",
        "target_duplicate",
        "target_missing",
        "field_difference",
        "identity_conflict",
        "authority_invalid",
    ]
    category_zh: str = Field(min_length=1)
    analysis_zh: str = Field(min_length=1)
    proposed_operation: OperationKind
    evidence_refs: tuple[str, ...]
    solution_zh: str = Field(min_length=1)
    risk: RiskLevel
    dependency_finding_ids: tuple[UUID, ...] = ()


class AgentFindingBatch(AgentSkillOutput):
    findings: tuple[AgentFinding, ...] = Field(max_length=50)


class GovernanceSolutionBatchInput(AgentSkillInput):
    findings: tuple[FindingReference, ...] = Field(max_length=50)


class GovernanceSolution(StrictContract):
    finding_id: UUID
    solution_zh: str = Field(min_length=1)
    operation: OperationKind
    risk: RiskLevel
    dependency_finding_ids: tuple[UUID, ...] = ()


class GovernanceSolutionBatch(AgentSkillOutput):
    solutions: tuple[GovernanceSolution, ...] = Field(max_length=50)


class ApprovalAggregationInput(AgentSkillInput):
    findings: tuple[FindingReference, ...]


class ApprovalGroup(StrictContract):
    group_key: str = Field(min_length=1)
    finding_ids: tuple[UUID, ...] = Field(min_length=1)
    operation: OperationKind
    risk: Literal["high"]
    reason_zh: str = Field(min_length=1)


class ApprovalGroupDraft(AgentSkillOutput):
    groups: tuple[ApprovalGroup, ...]


class ConflictInstructionInput(AgentSkillInput):
    conflict_id: UUID
    candidate_ids: tuple[UUID, ...] = Field(min_length=1)
    operator_instruction: str = Field(min_length=1)


class ConflictDecisionDraft(AgentSkillOutput):
    conflict_id: UUID
    decision: Literal["select_candidate", "treat_as_extra", "leave_unresolved"]
    selected_candidate_id: UUID | None = None
    interpretation_zh: str = Field(min_length=1)
    requires_second_confirmation: Literal[True]


class GovernanceExecutionInput(AgentSkillInput):
    plan_id: UUID
    operation_ids: tuple[UUID, ...] = Field(min_length=1)


class GovernanceExecutionOutcome(AgentSkillOutput):
    outcomes: tuple[OperationOutcome, ...]


class GovernanceReportInput(AgentSkillInput):
    outcome: Literal["completed", "terminated", "failed", "abnormal_input"]
    fact_refs: tuple[str, ...] = Field(min_length=1)


class AgentGovernanceReport(AgentSkillOutput):
    title_zh: str = Field(min_length=1)
    summary_zh: str = Field(min_length=1)
    fact_refs: tuple[str, ...] = Field(min_length=1)
    rollback_evidence_eligible: bool


class RollbackAssessmentInput(AgentSkillInput):
    original_task_id: UUID
    verified_execution_refs: tuple[str, ...] = Field(min_length=1)


class AgentRollbackAssessment(AgentSkillOutput):
    restorable_operation_ids: tuple[UUID, ...]
    already_restored_operation_ids: tuple[UUID, ...]
    conflict_operation_ids: tuple[UUID, ...]
    impact_zh: str = Field(min_length=1)
    requires_confirmation: Literal[True]


class RollbackExecutionInput(AgentSkillInput):
    restore_plan_id: UUID
    operation_ids: tuple[UUID, ...] = Field(min_length=1)


class AgentRollbackOutcome(AgentSkillOutput):
    outcomes: tuple[OperationOutcome, ...]


AGENT_SKILL_SCHEMAS: dict[str, type[BaseModel]] = {
    model.__name__: model
    for model in (
        SupervisorPhaseInput,
        SourceInspectionInput,
        NormalizeOrganizationBatchInput,
        CsvSchemaMappingInput,
        DatabaseSchemaMappingInput,
        ReconcileEntityBatchInput,
        GovernanceSolutionBatchInput,
        ApprovalAggregationInput,
        ConflictInstructionInput,
        GovernanceExecutionInput,
        GovernanceReportInput,
        RollbackAssessmentInput,
        RollbackExecutionInput,
        SupervisorContextV1,
        SupervisorPhaseDecision,
        SourceInspectionResult,
        NormalizedOrganizationBatch,
        CsvSchemaMappingOutput,
        DatabaseSchemaMappingOutput,
        AgentFindingBatch,
        GovernanceSolutionBatch,
        ApprovalGroupDraft,
        ConflictDecisionDraft,
        GovernanceExecutionOutcome,
        AgentGovernanceReport,
        AgentRollbackAssessment,
        AgentRollbackOutcome,
        SupervisorDecisionV1,
    )
}
