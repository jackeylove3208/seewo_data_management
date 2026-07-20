import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { type PropsWithChildren } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ingestionApi } from "../../api/ingestion";
import { reconciliationApi } from "../../api/reconciliation";
import { saveStoredTask } from "../../data/taskHistory";
import { TaskDetailPage } from "./TaskDetailPage";

function wrapper({ children }: PropsWithChildren) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}><MemoryRouter initialEntries={["/tasks/real-1"]}>{children}</MemoryRouter></QueryClientProvider>;
}

describe("real task detail", () => {
  beforeEach(() => {
    localStorage.clear();
    saveStoredTask({
      id: "real-1",
      title: "教师数据核对",
      createdAt: "2026-07-17T10:00:00Z",
      sourceFile: "third_party.csv",
      targetFile: "seewo.csv",
      sourceAccepted: 10,
      targetAccepted: 10,
      issueCount: 0,
      status: "processing",
      selectedEntityTypes: ["teacher"],
    });
  });
  afterEach(() => vi.restoreAllMocks());

  it("renders persisted AI progress and analysis activity", async () => {
    vi.spyOn(ingestionApi, "getTask").mockResolvedValue({
      id: "real-1",
      tenant_id: "school-1",
      scope_id: "all",
      status: "ready",
      stage: "differences_ready",
      entity_types: ["teacher"],
      snapshots: {
        authoritative: { accepted: 10, normalized_with_warning: 0, quarantined: 0, rejected: 0, quarantine_available: false },
        target: { accepted: 10, normalized_with_warning: 0, quarantined: 0, rejected: 0, quarantine_available: false },
      },
      workflow: {
        stage: "analysis",
        status: "pending",
        attempt: 1,
        processed: 2,
        total: 5,
        analysis: { total: 5, completed: 2, succeeded: 1, manual_review: 1, failed: 0 },
        error: null,
      },
      error: null,
    });
    vi.spyOn(reconciliationApi, "advance").mockImplementation(() => new Promise(() => undefined));
    vi.spyOn(reconciliationApi, "listDifferences").mockResolvedValue({ items: [], next_cursor: null });

    render(<Routes><Route path="/tasks/:taskId" element={<TaskDetailPage />} /></Routes>, { wrapper });

    expect(await screen.findByText("AI 分析中")).toBeInTheDocument();
    expect(screen.getByText("已完成 2 / 5")).toBeInTheDocument();
    expect(screen.getByText((_, element) => element?.tagName === "SMALL" && element.textContent?.includes("仅人工 1") === true)).toBeInTheDocument();
    expect(screen.queryByText("演示差异")).not.toBeInTheDocument();
  });
});
