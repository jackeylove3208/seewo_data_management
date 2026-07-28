import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { AgentGraphHumanGate } from "../../api/agent";
import { ConversationMediumRiskReviewCard } from "./ConversationMediumRiskReviewCard";

const mediumGates: AgentGraphHumanGate[] = [
  {
    id: "gate-medium-teacher",
    kind: "high_risk_approval",
    status: "pending",
    item_count: 1,
    risk: "medium",
    cursor: 11,
    membership_hash: "membership-teacher",
    actionable: true,
    items: [{
      finding_id: "finding-teacher",
      entity_kind: "teacher",
      entity_name: "张老师",
      entity_number: "T-001",
      source_locator: "database:seewo:T-001",
      operation_zh: "修改教师邮箱",
      issue_zh: "邮箱不一致",
      analysis_zh: "不在聊天中展示",
      solution_zh: "不在聊天中展示",
      changes: [{
        field: "email",
        field_zh: "邮箱",
        before: "old@example.test",
        after: "new@example.test",
      }],
    }],
  },
  {
    id: "gate-medium-department",
    kind: "high_risk_approval",
    status: "pending",
    item_count: 1,
    risk: "medium",
    cursor: 11,
    membership_hash: "membership-department",
    actionable: true,
    items: [{
      finding_id: "finding-department",
      entity_kind: "department",
      entity_name: "教务处",
      entity_number: "D-001",
      source_locator: "database:seewo:D-001",
      operation_zh: "修改部门名称",
      issue_zh: "名称不一致",
      analysis_zh: "不在聊天中展示",
      solution_zh: "不在聊天中展示",
      changes: [{
        field: "name",
        field_zh: "名称",
        before: "教导处",
        after: "教务处",
      }],
    }],
  },
];

describe("ConversationMediumRiskReviewCard", () => {
  it("defaults all items to approval and submits selected rejections once", async () => {
    const onSubmit = vi.fn().mockResolvedValue({
      "gate-medium-teacher": "rejected",
      "gate-medium-department": "approved",
    });
    const user = userEvent.setup();

    render(
      <ConversationMediumRiskReviewCard
        gates={mediumGates}
        onSubmit={onSubmit}
      />,
    );

    expect(screen.getByRole("region", { name: "中风险批量审核" })).toBeInTheDocument();
    expect(screen.getByText("中风险治理建议")).toBeInTheDocument();
    expect(screen.getByText("张老师（T-001）")).toBeInTheDocument();
    expect(screen.getByText("教务处（D-001）")).toBeInTheDocument();
    expect(screen.queryByText("不在聊天中展示")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "全部同意并继续" })).toBeInTheDocument();

    await user.click(screen.getByRole("checkbox", { name: "拒绝张老师（T-001）" }));
    await user.click(screen.getByRole("button", { name: "按当前选择继续（同意 1，拒绝 1）" }));

    expect(onSubmit).toHaveBeenCalledWith(
      mediumGates,
      new Set(["finding-teacher"]),
    );
    expect(await screen.findByText("已完成复核")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /继续/ })).not.toBeInTheDocument();
  });

  it("keeps completed gates read-only while submitting only remaining pending gates", async () => {
    const restoredGates: AgentGraphHumanGate[] = [
      {
        ...mediumGates[0],
        status: "approved",
        actionable: false,
        member_decisions: { "finding-teacher": "approved" },
      },
      mediumGates[1],
    ];
    const onSubmit = vi.fn().mockResolvedValue({
      "gate-medium-department": "approved",
    });
    const user = userEvent.setup();

    render(
      <ConversationMediumRiskReviewCard
        gates={restoredGates}
        onSubmit={onSubmit}
      />,
    );

    expect(screen.getByRole("checkbox", { name: "拒绝张老师（T-001）" })).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: "拒绝教务处（D-001）" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "全部同意并继续" }));

    expect(onSubmit).toHaveBeenCalledWith(
      [mediumGates[1]],
      new Set(),
    );
    expect(await screen.findByText("已完成复核")).toBeInTheDocument();
  });

  it("reflects persisted member decisions when a pending gate completes after refresh", () => {
    const onSubmit = vi.fn();
    const { rerender } = render(
      <ConversationMediumRiskReviewCard
        gates={[mediumGates[0]]}
        onSubmit={onSubmit}
      />,
    );
    const checkbox = screen.getByRole("checkbox", {
      name: "拒绝张老师（T-001）",
    });
    expect(checkbox).not.toBeChecked();

    rerender(
      <ConversationMediumRiskReviewCard
        gates={[{
          ...mediumGates[0],
          status: "rejected",
          actionable: false,
          member_decisions: { "finding-teacher": "rejected" },
        }]}
        onSubmit={onSubmit}
      />,
    );

    expect(checkbox).toBeChecked();
    expect(checkbox).toBeDisabled();
    expect(screen.getByText("已完成复核")).toBeInTheDocument();
  });
});
