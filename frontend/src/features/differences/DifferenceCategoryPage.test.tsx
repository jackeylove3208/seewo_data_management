import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi } from "vitest";

import { reconciliationApi, type DifferenceItem } from "../../api/reconciliation";
import { saveStoredTask } from "../../data/taskHistory";
import { DifferenceCategoryPage } from "./DifferenceCategoryPage";

describe("difference category detail", () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => vi.restoreAllMocks());

  it("selects one issue independently when a person has multiple problems", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/tasks/demo-001/differences/teacher"]}>
        <Routes>
          <Route path="/tasks/:taskId/differences/:entityType" element={<DifferenceCategoryPage />} />
        </Routes>
      </MemoryRouter>,
    );

    await user.click(screen.getByRole("button", { name: /张三/ }));
    await user.click(screen.getByRole("checkbox", { name: "选择张三的所属部门" }));

    expect(screen.getByText("已选择 1 人，共 1 个问题")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "选择张三的手机号" })).not.toBeChecked();
  });

  it("renders backend differences for a real task and opens analysis on demand", async () => {
    const user = userEvent.setup();
    saveStoredTask({
      id: "real-1",
      title: "教师核对",
      createdAt: "2026-07-17T10:00:00Z",
      sourceFile: "source.csv",
      targetFile: "target.csv",
      sourceAccepted: 1,
      targetAccepted: 1,
      issueCount: 1,
      status: "ready",
      selectedEntityTypes: ["teacher"],
    });
    const item: DifferenceItem = {
      id: "difference-1",
      task_id: "real-1",
      tenant_id: "school-1",
      entity_type: "teacher",
      difference_type: "attribute_conflict",
      proposed_action: "update",
      evidence: {
        source_snapshot_id: "source",
        target_snapshot_id: "target",
        source_entity_id: "source-person",
        target_entity_id: "target-person",
        mapping_id: "mapping",
        fields: [{ field: "phone", source_value: "13800000000", target_value: "13900000000", normalized_source: "13800000000", normalized_target: "13900000000", comparison: "attribute" }],
        match_evidence: [],
        source_payload: { name: "张老师" },
        target_payload: { name: "张老师" },
        related_entities: [],
        comparison_rule_version: "comparison-v1",
      },
      status: "open",
      version: 1,
      created_at: "2026-07-17T10:00:00Z",
      analysis_status: "pending",
      risk: null,
      execution_eligible: false,
      proposal_status: null,
      current_proposal_version: null,
    };
    vi.spyOn(reconciliationApi, "listDifferences").mockResolvedValue({ items: [item], next_cursor: null });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={["/tasks/real-1/differences/teacher"]}>
          <Routes><Route path="/tasks/:taskId/differences/:entityType" element={<DifferenceCategoryPage />} /></Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(await screen.findByText("13800000000")).toBeInTheDocument();
    expect(screen.getByText("13900000000")).toBeInTheDocument();
    expect(screen.queryByText("演示差异")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "查看 AI 分析" }));
    expect(screen.getByText("AI 正在分析这条差异")).toBeInTheDocument();
  });
});
