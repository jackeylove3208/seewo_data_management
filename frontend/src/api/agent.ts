import { requestJson } from "./client";

export type AgentEntityType = "department" | "student" | "teacher";
export type AgentPhase =
  | "intent_confirmed"
  | "acquire_school_lock"
  | "ingest_and_normalize"
  | "build_identity_work"
  | "analyze_batches"
  | "clarify_identity_conflicts"
  | "aggregate_risk_and_approvals"
  | "compile_execution_plan"
  | "execute_and_verify"
  | "generate_report"
  | "plan_restore"
  | "clarify_restore_conflicts"
  | "approve_restore"
  | "execute_restore"
  | "report_restore"
  | "terminal";

interface AgentConversation {
  id: string;
  status: "active" | "closed";
}

export interface AgentApiConnectionCard {
  provider_id: string;
  state: "configuration_required" | "pending" | "active" | "invalid" | "disabled";
  required_secret_fields: string[];
  connection_id?: string | null;
  display_name?: string | null;
  capabilities: Record<string, boolean>;
  visibility_summary: Record<string, string | number | boolean | null>;
  safe_error_code?: string | null;
}

export interface AgentApiConnectionConfiguration {
  provider_id: string;
  display_name: string;
  required_secret_fields: string[];
  secret: Record<string, string>;
  connection_id?: string | null;
}

export interface AgentIntent {
  title: string;
  entity_types: AgentEntityType[];
  source?: AgentConnectorSelection;
  target?: AgentConnectorSelection;
}

export interface AgentConnectorSelection {
  kind: "csv" | "api" | "database" | "local" | "remote_csv";
  upload_id?: string;
  configuration_id?: string;
  source_ref?: string;
  remote_source_id?: string;
  display_origin?: string;
}

export interface AgentStartConfirmation {
  title: string;
  summary: string;
  entity_types: AgentEntityType[];
}

interface AgentMessageResponse {
  accepted_message: string;
  message: string;
  intent: AgentIntent;
  start_confirmation?: AgentStartConfirmation;
  api_connection?: AgentApiConnectionCard | null;
}

interface AgentConversationMessage {
  id: string;
  role: "assistant" | "user";
  kind: "normal" | "guardrail" | "error";
  text: string;
  created_at: string;
}

interface AgentConversationCurrent extends AgentConversation {
  messages: AgentConversationMessage[];
  intent?: AgentIntent | null;
  start_confirmation?: AgentStartConfirmation | null;
  api_connection?: AgentApiConnectionCard | null;
  task?: AgentTask | null;
}

export interface AgentTask {
  id: string;
  workflow_version: string;
  task_kind?: "sync" | "rollback";
  parent_task_id?: string | null;
  phase: AgentPhase;
  status: string;
  title?: string;
  report_id?: string | null;
  rollback_eligible?: boolean;
  rollback_blocked_reason?: "already_rolled_back" | null;
  deletion_eligible?: boolean;
  error?: {
    code: string;
    message: string;
  } | null;
}

export interface AgentTaskEvent {
  id: string;
  cursor: string;
  type: string;
  phase?: AgentPhase;
  status?: string;
  payload: Record<string, unknown>;
  created_at: string;
}

interface AgentEventPage {
  cursor: string;
  events: AgentTaskEvent[];
}

export interface AgentGraphIdentityRecord {
  candidate_id?: string | null;
  entity_kind?: AgentEntityType | null;
  category?: string | null;
  name?: string | null;
  number?: string | null;
  class_name?: string | null;
  phone_masked?: string | null;
  email_masked?: string | null;
}

export interface AgentClarificationSubmission {
  decision: "select_candidate" | "treat_as_extra";
  selected_candidate_id: string | null;
  note: string | null;
  interpretation_zh: string;
  submitted_at: string;
  source: "structured_selection";
}

export interface AgentGraphIdentityConflict {
  clarification_id: string;
  status: string;
  summary_zh: string;
  subject: AgentGraphIdentityRecord;
  candidates: AgentGraphIdentityRecord[];
  allowed_outcomes: string[];
  interpretation_zh?: string | null;
  operator_submission?: AgentClarificationSubmission | null;
}

export interface AgentGraphHumanGate {
  id: string;
  kind: string;
  status: string;
  item_count: number;
  risk?: "medium" | "high" | null;
  cursor?: number;
  membership_hash?: string | null;
  member_decisions?: Record<string, "approved" | "rejected">;
  entity_kind?: AgentEntityType | null;
  operation?: string | null;
  issue_kind?: string | null;
  summary_zh?: string | null;
  risk_reason_zh?: string | null;
  actionable?: boolean;
  unavailable_reason_zh?: string | null;
  items?: AgentGraphApprovalItem[];
  conflicts?: AgentGraphIdentityConflict[];
}

interface AgentGraphApprovalChange {
  field: string;
  field_zh: string;
  before?: string | null;
  after?: string | null;
}

export interface AgentGraphApprovalItem {
  finding_id: string;
  entity_kind: AgentEntityType;
  entity_name?: string | null;
  entity_number?: string | null;
  class_name?: string | null;
  source_locator: string;
  source_row_number?: number | null;
  operation_zh: string;
  issue_zh: string;
  analysis_zh: string;
  solution_zh: string;
  changes: AgentGraphApprovalChange[];
}

export interface AgentGraphProgress {
  task_id: string;
  workflow_version: "agent-graph-v1";
  graph_version: string;
  graph_cursor: number;
  current_node: string;
  business_stage:
    | "data_ingestion"
    | "agent_analysis"
    | "governance_execution"
    | "report_and_rollback"
    | "terminal";
  current_action_zh: string;
  sub_agent_zh?: string | null;
  progress_completed?: number | null;
  progress_total?: number | null;
  status: string;
  can_terminate: boolean;
  termination_requested: boolean;
  human_gates: AgentGraphHumanGate[];
}

export interface AgentClarificationInterpretation {
  decision_id: string;
  status: string;
  task_id: string;
  decision: "select_candidate" | "treat_as_extra" | "leave_unresolved";
  selected_candidate_id: string | null;
  interpretation_zh?: string;
  requires_second_confirmation?: boolean;
}

export interface AgentClarificationConfirmation {
  status: string;
}

interface StructuredClarificationSelectionInput {
  decision: "select_candidate" | "treat_as_extra";
  selected_candidate_id: string | null;
  note: string | null;
  graph_cursor: number;
  idempotency_key: string;
}

export interface AgentConversationApi {
  currentConversation(): Promise<AgentConversationCurrent | null>;
  createConversation(): Promise<AgentConversation>;
  resetConversation(idempotencyKey: string): Promise<AgentConversation>;
  sendMessage(conversationId: string, message: string): Promise<AgentMessageResponse>;
  configureApiConnection?(
    configuration: AgentApiConnectionConfiguration,
  ): Promise<AgentApiConnectionCard>;
  startTask(
    conversationId: string,
    intent: AgentIntent,
    idempotencyKey: string,
    options?: AgentTaskStartOptions,
  ): Promise<AgentTask>;
  task?(taskId: string, signal?: AbortSignal): Promise<AgentTask>;
  events(taskId: string, cursor?: string, signal?: AbortSignal): Promise<AgentEventPage>;
  graph?(taskId: string, signal?: AbortSignal): Promise<AgentGraphProgress>;
  terminate(taskId: string): Promise<{ status: string }>;
  previewTermination?(taskId: string): Promise<AgentGraphHumanGate>;
  decideGraphGate?(
    taskId: string,
    gateId: string,
    decision: "approve" | "reject",
    reason?: string,
    review?: AgentGraphGateReview,
  ): Promise<{ gate_id: string; status: "approved" | "rejected"; graph_cursor: number }>;
  decideGraphGates?(
    taskId: string,
    decisions: AgentGraphGateBatchDecision[],
  ): Promise<{ decisions: AgentGraphGateDecisionResult[] }>;
  approveGroup?(taskId: string, groupId: string): Promise<unknown>;
  rejectGroup?(taskId: string, groupId: string, reason?: string): Promise<unknown>;
  clarify?(taskId: string, message: string): Promise<AgentClarificationInterpretation>;
  submitClarificationSelection?(
    taskId: string,
    clarificationId: string,
    selection: StructuredClarificationSelectionInput,
  ): Promise<AgentClarificationInterpretation>;
  confirmClarification?(
    taskId: string,
    decisionId: string,
  ): Promise<AgentClarificationConfirmation>;
}

export interface AgentManualTaskApi {
  startManualTask(
    intent: AgentIntent,
    idempotencyKey: string,
    options?: AgentTaskStartOptions,
  ): Promise<AgentTask>;
  localSources?(): Promise<AgentLocalSource[]>;
}

interface AgentTaskStartOptions {
  acceptCurrentTargetBaseline?: boolean;
}

export interface AgentLocalSource {
  source_ref: string;
  kind: "csv";
  writable_as_target: boolean;
}

interface AgentGraphGateReview {
  approved_finding_ids: string[];
  rejected_finding_ids: string[];
  graph_cursor: number;
  membership_hash: string;
}

interface AgentGraphGateDecisionResult {
  gate_id: string;
  status: "approved" | "rejected";
  graph_cursor: number;
}

interface AgentGraphGateBatchDecision {
  gate_id: string;
  decision: "approve" | "reject";
  reason?: string;
  approved_finding_ids?: string[];
  rejected_finding_ids?: string[];
  graph_cursor?: number;
  membership_hash?: string;
}

export interface AgentHistoryItem extends AgentTask {
  created_at: string;
  completed_at: string | null;
  termination_requested: boolean;
  issue_summary: { total: number; excluded: number };
  operation_summary: { succeeded: number; failed: number; blocked: number };
  rollback_eligible: boolean;
  deletion_eligible: boolean;
  entity_types?: AgentEntityType[];
  target_source?: {
    key: string;
    name: string;
    kind: "database" | "local" | "upload" | "unknown";
    identified: boolean;
  };
}

interface AgentHistoryPage {
  items: AgentHistoryItem[];
  next_cursor: string | null;
}

interface AgentReport {
  id: string;
  task_id: string;
  kind: "sync" | "rollback";
  terminal_state: string;
  facts: Record<string, unknown>;
  content: Record<string, unknown>;
  rollback_eligible: boolean;
  deletion_eligible: boolean;
  created_at: string;
}

const jsonHeaders = { "Content-Type": "application/json" };

async function createConversation() {
  return requestJson<AgentConversation>("/api/agent/conversations", {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify({}),
  });
}

async function currentConversation() {
  return requestJson<AgentConversationCurrent | null>("/api/agent/conversations/current");
}

async function resetConversation(idempotencyKey: string) {
  return requestJson<AgentConversation>("/api/agent/conversations/current/reset", {
    method: "POST",
    headers: { ...jsonHeaders, "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({}),
  });
}

async function sendMessage(conversationId: string, message: string) {
  return requestJson<AgentMessageResponse>(`/api/agent/conversations/${conversationId}/messages`, {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify({ message }),
  });
}

interface ApiConnectionResponse {
  id: string;
  provider_id: string;
  display_name: string;
  state: "pending" | "active" | "invalid" | "disabled";
  capabilities: Record<string, boolean>;
  visibility_summary: Record<string, string | number | boolean | null>;
  last_safe_error_code?: string | null;
}

async function configureApiConnection(
  configuration: AgentApiConnectionConfiguration,
): Promise<AgentApiConnectionCard> {
  let connectionId = configuration.connection_id ?? undefined;
  if (connectionId) {
    await requestJson(`/api/connectors/connections/${connectionId}/rotate-secret`, {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ secret: configuration.secret }),
    });
  } else {
    const session = await requestJson<{ id: string }>(
      "/api/connectors/configuration-sessions",
      {
        method: "POST",
        headers: jsonHeaders,
        body: JSON.stringify({ provider_id: configuration.provider_id }),
      },
    );
    const connection = await requestJson<ApiConnectionResponse>(
      "/api/connectors/connections",
      {
        method: "POST",
        headers: jsonHeaders,
        body: JSON.stringify({
          configuration_session_id: session.id,
          provider_id: configuration.provider_id,
          display_name: configuration.display_name,
          public_configuration: { person_entity_kind: "teacher" },
          secret: configuration.secret,
        }),
      },
    );
    connectionId = connection.id;
  }
  const tested = await requestJson<ApiConnectionResponse>(
    `/api/connectors/connections/${connectionId}/test`,
    {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({}),
    },
  );
  return {
    provider_id: tested.provider_id,
    state: tested.state,
    required_secret_fields: configuration.required_secret_fields,
    connection_id: tested.id,
    display_name: tested.display_name,
    capabilities: tested.capabilities,
    visibility_summary: tested.visibility_summary,
    safe_error_code: tested.last_safe_error_code,
  };
}

async function startTask(
  conversationId: string,
  intent: AgentIntent,
  idempotencyKey: string,
  options?: AgentTaskStartOptions,
) {
  const requestIntent = {
    ...intent,
    source: requestConnector(intent.source),
    target: requestConnector(intent.target),
  };
  return requestJson<AgentTask>(`/api/agent/conversations/${conversationId}/tasks`, {
    method: "POST",
    headers: taskStartHeaders(idempotencyKey, options),
    body: JSON.stringify(requestIntent),
  });
}

function requestConnector(connector?: AgentConnectorSelection) {
  if (!connector) return undefined;
  const { display_origin: displayOrigin, ...requestSelection } = connector;
  void displayOrigin;
  return requestSelection;
}

async function startManualTask(
  intent: AgentIntent,
  idempotencyKey: string,
  options?: AgentTaskStartOptions,
) {
  return requestJson<AgentTask>("/api/agent/tasks", {
    method: "POST",
    headers: taskStartHeaders(idempotencyKey, options),
    body: JSON.stringify(intent),
  });
}

function taskStartHeaders(
  idempotencyKey: string,
  options?: AgentTaskStartOptions,
) {
  return {
    ...jsonHeaders,
    "Idempotency-Key": idempotencyKey,
    ...(options?.acceptCurrentTargetBaseline
      ? { "X-Accept-Current-Target-Baseline": "true" }
      : {}),
  };
}

async function events(taskId: string, cursor?: string, signal?: AbortSignal) {
  const suffix = cursor ? `?cursor=${encodeURIComponent(cursor)}` : "";
  return requestJson<AgentEventPage>(`/api/agent/tasks/${taskId}/events${suffix}`, { signal });
}

async function graph(taskId: string, signal?: AbortSignal) {
  return requestJson<AgentGraphProgress>(`/api/agent/tasks/${taskId}/graph`, { signal });
}

async function decideGraphGate(
  taskId: string,
  gateId: string,
  decision: "approve" | "reject",
  reason?: string,
  review?: AgentGraphGateReview,
) {
  return requestJson<{ gate_id: string; status: "approved" | "rejected"; graph_cursor: number }>(
    `/api/agent/tasks/${taskId}/graph/gates/${gateId}/decision`,
    {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ decision, reason, ...review }),
    },
  );
}

async function decideGraphGates(
  taskId: string,
  decisions: AgentGraphGateBatchDecision[],
) {
  return requestJson<{ decisions: AgentGraphGateDecisionResult[] }>(
    `/api/agent/tasks/${taskId}/graph/gates/decisions`,
    {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ decisions }),
    },
  );
}

async function localSources() {
  return requestJson<AgentLocalSource[]>("/api/agent/local-sources");
}

async function terminate(taskId: string) {
  return requestJson<{ status: string }>(`/api/agent/tasks/${taskId}/terminate`, {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify({}),
  });
}

async function previewTermination(taskId: string) {
  return requestJson<AgentGraphHumanGate>(
    `/api/agent/tasks/${taskId}/termination-preview`,
    {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({}),
    },
  );
}

async function approveGroup(taskId: string, groupId: string) {
  return requestJson(`/api/agent/tasks/${taskId}/approval-groups/${groupId}/approve`, {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify({}),
  });
}

async function rejectGroup(taskId: string, groupId: string, reason?: string) {
  return requestJson(`/api/agent/tasks/${taskId}/approval-groups/${groupId}/reject`, {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify({ reason }),
  });
}

async function clarify(taskId: string, message: string) {
  return requestJson<AgentClarificationInterpretation>(`/api/agent/tasks/${taskId}/clarification`, {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify({ message }),
  });
}

async function submitClarificationSelection(
  taskId: string,
  clarificationId: string,
  selection: StructuredClarificationSelectionInput,
) {
  return requestJson<AgentClarificationInterpretation>(
    `/api/agent/tasks/${taskId}/clarifications/${clarificationId}/selection`,
    {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify(selection),
    },
  );
}

async function confirmClarification(taskId: string, decisionId: string) {
  return requestJson<AgentClarificationConfirmation>(`/api/agent/tasks/${taskId}/clarification/${decisionId}/confirm`, {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify({}),
  });
}

async function history(cursor?: string, signal?: AbortSignal) {
  const suffix = cursor ? `?cursor=${encodeURIComponent(cursor)}` : "";
  return requestJson<AgentHistoryPage>(`/api/agent/history${suffix}`, { signal });
}

async function task(taskId: string, signal?: AbortSignal) {
  return requestJson<AgentTask>(`/api/agent/tasks/${taskId}`, { signal });
}

async function report(taskId: string, signal?: AbortSignal) {
  return requestJson<AgentReport>(`/api/agent/tasks/${taskId}/report`, { signal });
}

async function deleteTask(taskId: string) {
  return requestJson<void>(`/api/agent/tasks/${taskId}`, { method: "DELETE" });
}

export interface AgentRollbackPreview {
  task_id: string;
  source_task_id: string;
  target_version_id: string;
  operation_count: number;
  state: "awaiting_confirmation" | "in_progress" | "completed" | "ended";
  message_zh: string;
  requires_confirmation: boolean;
}

async function previewRollback(taskId: string) {
  return requestJson<AgentRollbackPreview>(`/api/agent/tasks/${taskId}/rollback-preview`, {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify({}),
  });
}

async function confirmRollback(taskId: string) {
  return requestJson<AgentTask>(`/api/agent/rollback-tasks/${taskId}/confirm`, {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify({}),
  });
}

async function rejectRollback(taskId: string) {
  return requestJson<AgentTask>(`/api/agent/rollback-tasks/${taskId}/reject`, {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify({}),
  });
}

export const agentApi: AgentConversationApi & AgentManualTaskApi & {
  history: typeof history;
  task: typeof task;
  report: typeof report;
  deleteTask: typeof deleteTask;
  previewRollback: typeof previewRollback;
  confirmRollback: typeof confirmRollback;
  rejectRollback: typeof rejectRollback;
  graph: typeof graph;
  localSources: typeof localSources;
  decideGraphGate: typeof decideGraphGate;
  decideGraphGates: typeof decideGraphGates;
  previewTermination: typeof previewTermination;
  clarify: typeof clarify;
  submitClarificationSelection: typeof submitClarificationSelection;
  confirmClarification: typeof confirmClarification;
} = {
  currentConversation,
  createConversation,
  resetConversation,
  sendMessage,
  configureApiConnection,
  startTask,
  startManualTask,
  events,
  terminate,
  approveGroup,
  rejectGroup,
  clarify,
  submitClarificationSelection,
  confirmClarification,
  history,
  task,
  report,
  deleteTask,
  previewRollback,
  confirmRollback,
  rejectRollback,
  graph,
  localSources,
  decideGraphGate,
  decideGraphGates,
  previewTermination,
};
