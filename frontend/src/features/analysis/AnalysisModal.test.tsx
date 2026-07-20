import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { type PropsWithChildren } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { reconciliationApi, type AnalysisResult, type DifferenceItem } from "../../api/reconciliation";
import { ApiError } from "../../api/client";
import { AnalysisModal } from "./AnalysisModal";

const difference: DifferenceItem = {
  id: "difference-1",
  task_id: "task-1",
  tenant_id: "school-1",
  entity_type: "teacher",
  difference_type: "attribute_conflict",
  proposed_action: "update",
  evidence: {
    source_snapshot_id: "source-1",
    target_snapshot_id: "target-1",
    source_entity_id: "source-person",
    target_entity_id: "target-person",
    mapping_id: "mapping-1",
    fields: [{ field: "phone", source_value: "13800000000", target_value: "13900000000", normalized_source: "13800000000", normalized_target: "13900000000", comparison: "attribute" }],
    match_evidence: [],
    source_payload: { name: "张老师", phone: "13800000000" },
    target_payload: { name: "张老师", phone: "13900000000" },
    related_entities: [],
    comparison_rule_version: "comparison-v1",
  },
  status: "open",
  version: 1,
  created_at: "2026-07-17T10:00:00Z",
  analysis_status: "succeeded",
  risk: "low",
  execution_eligible: true,
  proposal_status: null,
  current_proposal_version: null,
};

const analysis: AnalysisResult = {
  id: "analysis-1",
  difference_id: difference.id,
  difference_version: 1,
  analysis_version: "analysis-v2",
  status: "succeeded",
  output: {
    cause: "手机号来自不同更新时间的数据快照",
    evidence_summary: "三方系统手机号比希沃记录更新",
    manual_only: false,
    manual_reason: null,
    options: [
      {
        option_id: "option-1",
        operation_type: "update",
        target_entity_id: "target-person",
        proposed_changes: [{ field: "phone", before: "13900000000", after: "13800000000" }],
        rationale: "采用权威系统的当前手机号",
        evidence_refs: ["field:phone"],
        risk: "low",
        confidence: 0.94,
        preconditions: ["目标记录版本未变化"],
        recommended: true,
      },
      {
        option_id: "option-2",
        operation_type: "skip",
        target_entity_id: "target-person",
        proposed_changes: [],
        rationale: "等待下一次完整同步再处理",
        evidence_refs: ["field:phone"],
        risk: "medium",
        confidence: 0.7,
        preconditions: [],
        recommended: false,
      },
    ],
  },
  failure_code: null,
  attempt_count: 1,
  provenance: {
    provider: "enterprise-gateway",
    model: "enterprise-model",
    skill_name: "analyze-data-difference",
    skill_version: "1.0.0",
    prompt_version: "analysis-prompt-v2",
    tool_trace_ids: [],
    gateway_request_ids: ["request-1"],
    usage: { input_tokens: 10, output_tokens: 20 },
    generated_at: "2026-07-17T10:00:00Z",
  },
};

function wrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return function Provider({ children }: PropsWithChildren) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

describe("AnalysisModal", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows analysis animation while the selected difference is pending", () => {
    render(<AnalysisModal open difference={{ ...difference, analysis_status: "pending" }} onClose={() => undefined} />, { wrapper: wrapper() });

    expect(screen.getByText("AI 正在分析这条差异")).toBeInTheDocument();
    expect(screen.getByTestId("analysis-animation")).toBeInTheDocument();
  });

  it("previews and confirms one persisted AI option", async () => {
    const user = userEvent.setup();
    vi.spyOn(reconciliationApi, "getAnalysis").mockResolvedValue(analysis);
    vi.spyOn(reconciliationApi, "previewAIProposal").mockResolvedValue({
      difference_id: difference.id,
      difference_version: 1,
      proposal_source: "ai",
      operation_type: "update",
      target_entity_id: "target-person",
      changes: analysis.output!.options[0].proposed_changes,
      rationale: analysis.output!.options[0].rationale,
      evidence_refs: ["field:phone"],
      risk: "low",
    });
    const confirm = vi.spyOn(reconciliationApi, "confirmAIProposal").mockResolvedValue({
      id: "proposal-1",
      task_id: "task-1",
      tenant_id: "school-1",
      analysis_id: "analysis-1",
      analysis_version: "analysis-v2",
      proposal_version: 1,
      created_by: "operator-1",
      created_at: "2026-07-17T10:01:00Z",
      status: "pending_execution",
      supersedes_id: null,
      difference_id: difference.id,
      difference_version: 1,
      proposal_source: "ai",
      operation_type: "update",
      target_entity_id: "target-person",
      changes: analysis.output!.options[0].proposed_changes,
      rationale: analysis.output!.options[0].rationale,
      evidence_refs: ["field:phone"],
      risk: "low",
    });

    render(<AnalysisModal open difference={difference} onClose={() => undefined} />, { wrapper: wrapper() });
    expect(await screen.findByText("手机号来自不同更新时间的数据快照")).toBeInTheDocument();
    expect(screen.getAllByText("推荐")).toHaveLength(1);
    await user.click(screen.getAllByRole("button", { name: "采用并预览" })[0]);
    expect(await screen.findByText("方案修改预览")).toBeInTheDocument();
    expect(screen.getByText("13800000000")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认生成待执行方案" }));

    await waitFor(() => expect(confirm).toHaveBeenCalledWith("difference-1", {
      analysis_id: "analysis-1",
      option_id: "option-1",
      expected_difference_version: 1,
    }));
    expect(await screen.findByText("已进入待治理执行")).toBeInTheDocument();
  });

  it("shows only the manual path for manual-only analysis", async () => {
    vi.spyOn(reconciliationApi, "getAnalysis").mockResolvedValue({
      ...analysis,
      status: "manual_review",
      output: {
        cause: "无法确认人员身份",
        evidence_summary: "候选记录得分相同",
        manual_only: true,
        manual_reason: "信息不足，需要人工核实",
        options: [],
      },
    });

    render(<AnalysisModal open difference={{ ...difference, analysis_status: "manual_review" }} onClose={() => undefined} />, { wrapper: wrapper() });

    expect(await screen.findByText("信息不足，需要人工核实")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "采用并预览" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "人工修改" })).toBeInTheDocument();
  });

  it("builds a manual proposal from the backend editor schema", async () => {
    const user = userEvent.setup();
    vi.spyOn(reconciliationApi, "getAnalysis").mockResolvedValue(analysis);
    vi.spyOn(reconciliationApi, "getEditorSchema").mockResolvedValue({
      entity_type: "teacher",
      fields: [
        { name: "phone", label: "Phone", field_type: "phone", required: false },
        { name: "email", label: "Email", field_type: "email", required: false },
      ],
    });
    const preview = vi.spyOn(reconciliationApi, "previewManualProposal").mockResolvedValue({
      difference_id: difference.id,
      difference_version: 1,
      proposal_source: "operator",
      operation_type: "update",
      target_entity_id: "target-person",
      changes: [{ field: "phone", before: "13900000000", after: "13700000000" }],
      rationale: "人工核对校内通讯录后确认",
      evidence_refs: ["field:phone"],
      risk: "medium",
    });
    vi.spyOn(reconciliationApi, "confirmManualProposal").mockResolvedValue({
      id: "proposal-manual",
      task_id: "task-1",
      tenant_id: "school-1",
      analysis_id: "analysis-1",
      analysis_version: "analysis-v2",
      proposal_version: 1,
      created_by: "operator-1",
      created_at: "2026-07-17T10:01:00Z",
      status: "pending_execution",
      supersedes_id: null,
      difference_id: difference.id,
      difference_version: 1,
      proposal_source: "operator",
      operation_type: "update",
      target_entity_id: "target-person",
      changes: [{ field: "phone", before: "13900000000", after: "13700000000" }],
      rationale: "人工核对校内通讯录后确认",
      evidence_refs: ["field:phone"],
      risk: "medium",
    });

    render(<AnalysisModal open difference={difference} onClose={() => undefined} />, { wrapper: wrapper() });
    await user.click(await screen.findByRole("button", { name: "人工修改" }));
    const phone = await screen.findByRole("textbox", { name: "Phone" });
    await user.clear(phone);
    await user.type(phone, "13700000000");
    await user.type(screen.getByRole("textbox", { name: "修改原因" }), "人工核对校内通讯录后确认");
    await user.click(screen.getByRole("button", { name: "预览人工方案" }));

    await waitFor(() => expect(preview).toHaveBeenCalledWith("difference-1", expect.objectContaining({
      changes: { phone: "13700000000" },
      rationale: "人工核对校内通讯录后确认",
    })));
    expect(await screen.findByText("方案修改预览")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认生成待执行方案" }));
    expect(await screen.findByText("已进入待治理执行")).toBeInTheDocument();
  });

  it("leaves confirmation and requests fresh evidence after a version conflict", async () => {
    const user = userEvent.setup();
    vi.spyOn(reconciliationApi, "getAnalysis").mockResolvedValue(analysis);
    vi.spyOn(reconciliationApi, "previewAIProposal").mockResolvedValue({
      difference_id: difference.id,
      difference_version: 1,
      proposal_source: "ai",
      operation_type: "update",
      target_entity_id: "target-person",
      changes: analysis.output!.options[0].proposed_changes,
      rationale: analysis.output!.options[0].rationale,
      evidence_refs: ["field:phone"],
      risk: "low",
    });
    vi.spyOn(reconciliationApi, "confirmAIProposal").mockRejectedValue(new ApiError("difference version is stale", 409));

    render(<AnalysisModal open difference={difference} onClose={() => undefined} />, { wrapper: wrapper() });
    await user.click((await screen.findAllByRole("button", { name: "采用并预览" }))[0]);
    await user.click(await screen.findByRole("button", { name: "确认生成待执行方案" }));

    expect(await screen.findByText("数据版本已变化，请重新打开分析后确认。" )).toBeInTheDocument();
    expect(screen.queryByText("方案修改预览")).not.toBeInTheDocument();
  });
});
