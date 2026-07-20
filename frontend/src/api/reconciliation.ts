import type { EntityType } from "../types/domain";
import { requestJson } from "./client";

export type WorkflowStage = "ingestion" | "matching" | "differences" | "analysis" | "complete";
export type WorkflowStatus = "pending" | "running" | "succeeded" | "failed";
export type AnalysisStatus = "pending" | "succeeded" | "manual_review" | "failed";
export type RiskLevel = "low" | "medium" | "high";
export type OperationType = "create" | "update" | "move" | "disable" | "skip" | "manual_review";

export interface WorkflowError {
  code: string;
  message: string;
  retryable: boolean;
}

export interface AnalysisProgress {
  job_id?: string | null;
  total: number;
  completed: number;
  succeeded: number;
  manual_review: number;
  failed: number;
}

export interface WorkflowState {
  stage: WorkflowStage;
  status: WorkflowStatus;
  attempt: number;
  processed: number;
  total: number;
  analysis: AnalysisProgress;
  error: WorkflowError | null;
}

export interface WorkflowAdvanceResponse {
  task_id: string;
  workflow: WorkflowState;
}

export type DifferenceType =
  | "seewo_missing"
  | "seewo_redundant"
  | "attribute_conflict"
  | "structure_conflict"
  | "duplicate_conflict";

export interface FieldDifference {
  field: string;
  source_value: unknown;
  target_value: unknown;
  normalized_source: unknown;
  normalized_target: unknown;
  comparison: "attribute" | "structure" | "duplicate";
}

export interface DifferenceEvidence {
  source_snapshot_id: string;
  target_snapshot_id: string;
  source_entity_id: string | null;
  target_entity_id: string | null;
  mapping_id: string | null;
  fields: FieldDifference[];
  match_evidence: Array<Record<string, unknown>>;
  source_payload: Record<string, unknown> | null;
  target_payload: Record<string, unknown> | null;
  related_entities: Array<Record<string, unknown>>;
  comparison_rule_version: string;
}

export interface DifferenceItem {
  id: string;
  task_id: string;
  tenant_id: string;
  entity_type: EntityType;
  difference_type: DifferenceType;
  proposed_action: OperationType;
  evidence: DifferenceEvidence;
  status: string;
  version: number;
  created_at: string;
  analysis_status: AnalysisStatus;
  risk: RiskLevel | null;
  execution_eligible: boolean;
  proposal_status: "pending_execution" | null;
  current_proposal_version: number | null;
}

export interface DifferencePage {
  items: DifferenceItem[];
  next_cursor: string | null;
}

export interface ProposedFieldChange {
  field: string;
  before: unknown;
  after: unknown;
}

export interface GovernanceOption {
  option_id: string;
  operation_type: OperationType;
  target_entity_id: string | null;
  proposed_changes: ProposedFieldChange[];
  rationale: string;
  evidence_refs: string[];
  risk: RiskLevel;
  confidence: number;
  preconditions: string[];
  recommended: boolean;
}

export interface CauseAnalysisV2 {
  cause: string;
  evidence_summary: string;
  manual_only: boolean;
  manual_reason: string | null;
  options: GovernanceOption[];
}

export type ResolutionMode = "auto_executable" | "needs_information" | "manual_only";

interface ResolutionBase {
  solution_id: string;
  title: string;
  rationale: string;
  risk: RiskLevel;
  risk_reason: string;
  confidence: number;
  evidence_refs: string[];
  preconditions: string[];
  recommended: boolean;
}

export interface AutoExecutableResolution extends ResolutionBase {
  mode: "auto_executable";
  action: {
    operation_type: OperationType;
    target_entity_id: string | null;
    proposed_changes: ProposedFieldChange[];
  };
}

export interface NeedsInformationResolution extends ResolutionBase {
  mode: "needs_information";
  information_requests: Array<{
    request_type: string;
    question: string;
    reason: string;
    source_hint: string;
  }>;
}

export interface ManualResolution extends ResolutionBase {
  mode: "manual_only";
  manual_steps: Array<{ order: number; instruction: string }>;
}

export type ResolutionPath = AutoExecutableResolution | NeedsInformationResolution | ManualResolution;

export interface CauseAnalysisV3 {
  locale: "zh-CN";
  issue_title: string;
  cause_summary: string;
  evidence_summary: string;
  business_impact: string;
  recommended_solution_id: string;
  solutions: ResolutionPath[];
}

export interface AnalysisResult {
  id: string;
  difference_id: string;
  difference_version: number;
  analysis_version: string;
  status: AnalysisStatus;
  output: CauseAnalysisV2 | CauseAnalysisV3 | null;
  failure_code: string | null;
  attempt_count: number;
  provenance: {
    provider: string;
    model: string;
    skill_name: string;
    skill_version: string;
    prompt_version: string;
    tool_trace_ids: string[];
    gateway_request_ids: string[];
    usage: { input_tokens: number; output_tokens: number };
    generated_at: string;
  };
}

export interface EntityEditorField {
  name: string;
  label: string;
  field_type: "text" | "email" | "phone" | "status" | "relation";
  required: boolean;
}

export interface EntityEditorSchema {
  entity_type: EntityType;
  fields: EntityEditorField[];
}

export interface AIProposalRequest {
  analysis_id: string;
  option_id: string;
  expected_difference_version: number;
}

export interface ManualProposalRequest {
  expected_difference_version: number;
  operation_type: OperationType;
  target_entity_id: string | null;
  changes: Record<string, unknown>;
  rationale: string;
}

export interface GovernanceProposalPreview {
  difference_id: string;
  difference_version: number;
  proposal_source: "ai" | "operator";
  operation_type: OperationType;
  target_entity_id: string | null;
  changes: ProposedFieldChange[];
  rationale: string;
  evidence_refs: string[];
  risk: RiskLevel;
}

export interface GovernanceProposal extends GovernanceProposalPreview {
  id: string;
  task_id: string;
  tenant_id: string;
  analysis_id: string;
  analysis_version: string;
  proposal_version: number;
  created_by: string;
  created_at: string;
  status: "pending_execution" | "superseded";
  supersedes_id: string | null;
}

export interface DifferenceFilters {
  entity_type?: EntityType;
  difference_type?: DifferenceType;
  analysis_status?: AnalysisStatus;
  risk?: RiskLevel;
  resolution_status?: string;
  cursor?: string;
  limit?: number;
}

export type AnalysisJobStatus = "queued" | "running" | "completed" | "completed_with_failures" | "canceled";

export interface AnalysisJobProgress {
  job_id: string;
  task_id: string;
  status: AnalysisJobStatus;
  total: number;
  completed: number;
  succeeded: number;
  manual_required: number;
  needs_information: number;
  manual_only: number;
  failed: number;
  proposal_ready: number;
  last_error: string | null;
  updated_at: string;
}

export interface EntityIssueSummary {
  entity_type: EntityType;
  issue_count: number;
  proposal_ready: number;
  needs_information: number;
  manual_only: number;
  failed: number;
}

export interface TaskAnalysisSummary {
  task_id: string;
  analysis_job_id: string | null;
  job_status: AnalysisJobStatus | null;
  terminal: boolean;
  entity_types: EntityIssueSummary[];
}

export type BatchExclusionReason = "high_risk" | "needs_information" | "manual_only" | "analysis_failed" | "stale" | "existing_proposal" | "no_recommended_action";

export interface BatchPreviewRequest {
  analysis_job_id: string;
  entity_type?: EntityType;
}

export interface BatchPreviewItem {
  difference_id: string;
  difference_version: number;
  analysis_id: string;
  solution_id: string;
  entity_type: EntityType;
  title: string;
  operation_type: OperationType;
  changes: ProposedFieldChange[];
  risk: RiskLevel;
}

export interface BatchExcludedItem {
  difference_id: string;
  entity_type: EntityType;
  reason: BatchExclusionReason;
  reason_label: string;
}

export interface BatchProposalPreview {
  task_id: string;
  analysis_job_id: string;
  preview_token: string;
  included: BatchPreviewItem[];
  excluded: BatchExcludedItem[];
}

export interface ConfirmBatchProposalRequest {
  preview_token: string;
  idempotency_key: string;
}

export interface BatchProposalResult {
  task_id: string;
  created: number;
  skipped: number;
  failed: number;
  items: Array<{
    difference_id: string;
    status: "created" | "skipped" | "failed";
    proposal_id: string | null;
    reason: string | null;
  }>;
}

function post<T>(path: string, body?: unknown, signal?: AbortSignal) {
  return requestJson<T>(path, {
    method: "POST",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });
}

function get<T>(path: string, signal?: AbortSignal) {
  return requestJson<T>(path, { signal });
}

function advance(taskId: string, signal?: AbortSignal) {
  return post<WorkflowAdvanceResponse>(`/api/reconciliation-tasks/${taskId}/workflow/advance`, undefined, signal);
}

function retry(taskId: string, signal?: AbortSignal) {
  return post<WorkflowAdvanceResponse>(`/api/reconciliation-tasks/${taskId}/workflow/retry`, undefined, signal);
}

function listDifferences(taskId: string, filters: DifferenceFilters = {}, signal?: AbortSignal) {
  const query = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") query.set(key, String(value));
  });
  const suffix = query.size ? `?${query.toString()}` : "";
  return get<DifferencePage>(`/api/reconciliation-tasks/${taskId}/differences${suffix}`, signal);
}

function getDifference(differenceId: string, signal?: AbortSignal) {
  return get<DifferenceItem>(`/api/differences/${differenceId}`, signal);
}

function getAnalysis(differenceId: string, signal?: AbortSignal) {
  return get<AnalysisResult>(`/api/differences/${differenceId}/analysis`, signal);
}

function getEditorSchema(entityType: EntityType, signal?: AbortSignal) {
  return get<EntityEditorSchema>(`/api/entity-editor-schemas/${entityType}`, signal);
}

function previewAIProposal(differenceId: string, body: AIProposalRequest) {
  return post<GovernanceProposalPreview>(`/api/differences/${differenceId}/proposals/from-analysis/preview`, body);
}

function confirmAIProposal(differenceId: string, body: AIProposalRequest) {
  return post<GovernanceProposal>(`/api/differences/${differenceId}/proposals/from-analysis`, body);
}

function previewManualProposal(differenceId: string, body: ManualProposalRequest) {
  return post<GovernanceProposalPreview>(`/api/differences/${differenceId}/proposals/manual/preview`, body);
}

function confirmManualProposal(differenceId: string, body: ManualProposalRequest) {
  return post<GovernanceProposal>(`/api/differences/${differenceId}/proposals/manual`, body);
}

function listProposals(differenceId: string, signal?: AbortSignal) {
  return get<GovernanceProposal[]>(`/api/differences/${differenceId}/proposals`, signal);
}

function createAnalysisJob(taskId: string, idempotencyKey: string, signal?: AbortSignal) {
  return requestJson<AnalysisJobProgress>(`/api/reconciliation-tasks/${taskId}/analysis-jobs`, {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    signal,
  });
}

function getAnalysisJob(jobId: string, signal?: AbortSignal) {
  return get<AnalysisJobProgress>(`/api/analysis-jobs/${jobId}`, signal);
}

function retryAnalysisJob(jobId: string) {
  return post<AnalysisJobProgress>(`/api/analysis-jobs/${jobId}/retry`);
}

function cancelAnalysisJob(jobId: string) {
  return post<AnalysisJobProgress>(`/api/analysis-jobs/${jobId}/cancel`);
}

function getAnalysisSummary(taskId: string, signal?: AbortSignal) {
  return get<TaskAnalysisSummary>(`/api/reconciliation-tasks/${taskId}/analysis-summary`, signal);
}

function previewProposalBatch(taskId: string, body: BatchPreviewRequest) {
  return post<BatchProposalPreview>(`/api/reconciliation-tasks/${taskId}/proposal-batches/preview`, body);
}

function confirmProposalBatch(taskId: string, body: ConfirmBatchProposalRequest) {
  return post<BatchProposalResult>(`/api/reconciliation-tasks/${taskId}/proposal-batches/confirm`, body);
}

export const reconciliationApi = {
  advance,
  retry,
  listDifferences,
  getDifference,
  getAnalysis,
  getEditorSchema,
  previewAIProposal,
  confirmAIProposal,
  previewManualProposal,
  confirmManualProposal,
  listProposals,
  createAnalysisJob,
  getAnalysisJob,
  retryAnalysisJob,
  cancelAnalysisJob,
  getAnalysisSummary,
  previewProposalBatch,
  confirmProposalBatch,
};
