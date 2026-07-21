import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";

import { ExecutionDetailPage } from "./ExecutionDetailPage";

afterEach(() => vi.restoreAllMocks());

function json(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/executions/exec-1"]}>
        <Routes><Route path="/executions/:executionId" element={<ExecutionDetailPage />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

it("uses the task timeline current version and shows immutable execution facts", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const path = String(input);
    if (path.endsWith("/reports")) return json([]);
    if (path.includes("target-versions")) return json([
      { id: "version-1", parent_version_id: null, task_id: "task-1", batch_id: null, content_hash: "a".repeat(64), created_at: "2026-07-20T08:00:00Z" },
      { id: "version-2", parent_version_id: "version-1", task_id: "task-1", batch_id: "exec-1", content_hash: "b".repeat(64), created_at: "2026-07-20T09:00:00Z" },
      { id: "version-3", parent_version_id: "version-2", task_id: "task-1", batch_id: "exec-2", content_hash: "c".repeat(64), created_at: "2026-07-20T10:00:00Z" },
    ]);
    return json({
      id: "exec-1", task_id: "task-1", plan_id: "plan-1", plan_version: 1,
      source_snapshot_id: "source-1", target_snapshot_id: "target-1",
      status: "succeeded", confirmed_by: "demo-operator", confirmed_at: "2026-07-20T09:00:00Z",
      input_target_version_id: "version-1", output_target_version_ids: ["version-2"],
      operations: [{ record_id: "record-1", operation_id: "operation-1", proposal_id: "proposal-1", proposal_version: 1, proposal_source: "ai", proposal_created_by: "demo-operator", difference_id: "difference-1", difference_version: 1, operation_type: "update", entity_type: "teacher", target_source_identifier: "teacher-1", before: { name: "旧名称" }, after: { name: "新名称" }, risk: "medium", attempts: [{ status: "succeeded" }] }],
      audit_events: [{ id: "audit-1", operation_id: null, actor_id: "demo-operator", event_type: "batch_execution_finished", details: {}, created_at: "2026-07-20T09:01:00Z" }],
      permitted_actions: ["download", "report", "restore"],
    });
  });

  renderPage();

  expect(await screen.findByRole("heading", { name: "执行与恢复" })).toBeInTheDocument();
  expect(screen.getByText("demo-operator")).toBeInTheDocument();
  expect(screen.getByText("旧名称", { exact: false })).toBeInTheDocument();
  expect(screen.getByText("batch_execution_finished")).toBeInTheDocument();
  expect(await screen.findByRole("button", { name: /恢复到 version-2/i })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /恢复到 version-3/i })).not.toBeInTheDocument();
});

it("reviews AI fallback then confirms and executes one restore batch", async () => {
  const calls: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    calls.push(`${init?.method ?? "GET"} ${path}`);
    if (path.endsWith("/reports")) return json([]);
    if ((init?.method ?? "GET") === "GET" && path.includes("target-versions")) return json([
      { id: "version-1", parent_version_id: null, task_id: "task-1", batch_id: null, content_hash: "a".repeat(64), created_at: "2026-07-20T08:00:00Z" },
      { id: "version-2", parent_version_id: "version-1", task_id: "task-1", batch_id: "exec-1", content_hash: "b".repeat(64), created_at: "2026-07-20T09:00:00Z" },
    ]);
    if (path.endsWith("/restore-preview")) return json({
      task_id: "task-1", restore_request_id: "restore-1", source_version_id: "version-2", semantic_source_version_id: "version-2", target_version_id: "version-1", preview_hash: "p".repeat(64), allowed: true, conflicts: [], covered_execution_ids: ["exec-1"], explanation: null, explanation_state: "unavailable",
      operations: [{ id: "restore-op-1", operation_type: "update", entity_type: "teacher", target_source_identifier: "teacher-1", before: { name: "新名称" }, after: { name: "旧名称" }, dependencies: [], risk: "high", compensation_for: "operation-1" }],
    });
    if (path.endsWith("/api/restores")) return json({ restore_request_id: "restore-1", batch_id: "restore-batch-1", plan_id: "restore-plan-1", input_target_version_id: "version-2", confirmed_by: "demo-operator", status: "confirmed" }, 202);
    if (path.endsWith("/api/restores/restore-1/execute")) return json({ id: "restore-batch-1", status: "succeeded", output_target_version_id: "version-3" }, 202);
    return json({ id: "exec-1", task_id: "task-1", plan_id: "plan-1", plan_version: 1, source_snapshot_id: "source-1", target_snapshot_id: "target-1", status: "succeeded", confirmed_by: "demo-operator", confirmed_at: "2026-07-20T09:00:00Z", input_target_version_id: "version-1", output_target_version_ids: ["version-2"], operations: [], audit_events: [], permitted_actions: ["report", "restore"] });
  });
  const user = userEvent.setup();
  renderPage();

  await user.click(await screen.findByRole("button", { name: /恢复到 version-1/i }));
  expect(await screen.findByText(/AI 影响说明不可用/)).toBeInTheDocument();
  expect(screen.getByText(/update · teacher · high/)).toBeInTheDocument();
  await user.click(screen.getByRole("checkbox"));
  await user.click(screen.getByRole("button", { name: "确认并执行恢复" }));

  expect(await screen.findByRole("link", { name: /查看执行 restore-batch-1/ })).toBeInTheDocument();
  await waitFor(() => {
    expect(calls.some((call) => call === "POST /api/restores")).toBe(true);
    expect(calls.some((call) => call === "POST /api/restores/restore-1/execute")).toBe(true);
  });
});

it("keeps the confirmed compensation batch link when execution fails", async () => {
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const path = String(input);
    if (path.endsWith("/reports")) return json([]);
    if ((init?.method ?? "GET") === "GET" && path.includes("target-versions")) return json([
      { id: "version-1", parent_version_id: null, task_id: "task-1", batch_id: null, content_hash: "a".repeat(64), created_at: "2026-07-20T08:00:00Z" },
      { id: "version-2", parent_version_id: "version-1", task_id: "task-1", batch_id: "exec-1", content_hash: "b".repeat(64), created_at: "2026-07-20T09:00:00Z" },
    ]);
    if (path.endsWith("/restore-preview")) return json({ task_id: "task-1", restore_request_id: "restore-1", source_version_id: "version-2", semantic_source_version_id: "version-2", target_version_id: "version-1", preview_hash: "p".repeat(64), allowed: true, conflicts: [], covered_execution_ids: ["exec-1"], explanation: null, explanation_state: "unavailable", operations: [{ id: "restore-op-1", operation_type: "update", entity_type: "teacher", target_source_identifier: "teacher-1", before: {}, after: {}, dependencies: [], risk: "high", compensation_for: "operation-1" }] });
    if (path.endsWith("/api/restores")) return json({ restore_request_id: "restore-1", batch_id: "restore-batch-1", plan_id: "restore-plan-1", input_target_version_id: "version-2", confirmed_by: "demo-operator", status: "confirmed" }, 202);
    if (path.endsWith("/api/restores/restore-1/execute")) return json({ detail: "connector failed" }, 409);
    return json({ id: "exec-1", task_id: "task-1", plan_id: "plan-1", plan_version: 1, source_snapshot_id: "source-1", target_snapshot_id: "target-1", status: "succeeded", confirmed_by: "demo-operator", confirmed_at: "2026-07-20T09:00:00Z", input_target_version_id: "version-1", output_target_version_ids: ["version-2"], operations: [], audit_events: [], permitted_actions: ["report", "restore"] });
  });
  const user = userEvent.setup();
  renderPage();

  await user.click(await screen.findByRole("button", { name: /恢复到 version-1/i }));
  await user.click(await screen.findByRole("checkbox"));
  await user.click(screen.getByRole("button", { name: "确认并执行恢复" }));

  expect(await screen.findByRole("link", { name: /查看执行 restore-batch-1/ })).toBeInTheDocument();
  expect(await screen.findByRole("alert")).toHaveTextContent("补偿批次已保留");
});
