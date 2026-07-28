import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { AgentGraphHumanGate } from "../../api/agent";
import { ConversationRiskApprovalCard } from "./ConversationRiskApprovalCard";

function gate(status: "pending" | "approved"): AgentGraphHumanGate {
  return {
    id: "gate-1",
    kind: "high_risk_approval",
    status,
    item_count: 1,
    risk: "high",
    cursor: 3,
    membership_hash: "membership-1",
    actionable: status === "pending",
    items: [{
      finding_id: "finding-1",
      entity_kind: "student",
      entity_name: "陈同学",
      entity_number: "S-001",
      source_locator: "database:seewo-mysql:S-001",
      operation_zh: "修改学生手机号",
      issue_zh: "手机号错误",
      analysis_zh: "隐藏分析",
      solution_zh: "隐藏方案",
      changes: [{
        field: "phone",
        field_zh: "电话",
        before: "13800000000",
        after: "13900000000",
      }],
    }],
  };
}

describe("ConversationRiskApprovalCard", () => {
  it("reflects a decision completed from another task view", () => {
    const onDecide = vi.fn();
    const view = render(
      <ConversationRiskApprovalCard gate={gate("pending")} onDecide={onDecide} />,
    );

    expect(screen.getByRole("button", { name: "同意高风险操作" })).toBeInTheDocument();
    view.rerender(
      <ConversationRiskApprovalCard gate={gate("approved")} onDecide={onDecide} />,
    );

    expect(screen.getByText("已同意")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "同意高风险操作" })).not.toBeInTheDocument();
  });

  it.each([
    ["同意高风险操作", "approved"],
    ["拒绝高风险操作", "rejected"],
  ] as const)(
    "advances to the next pending risk after clicking %s",
    async (buttonName, result) => {
      const user = userEvent.setup();
      const onDecide = vi.fn().mockResolvedValue(result);
      const firstGate = gate("pending");
      const secondGate = {
        ...gate("pending"),
        id: "gate-2",
        membership_hash: "membership-2",
      };
      const { container } = render(
        <>
          <ConversationRiskApprovalCard gate={firstGate} onDecide={onDecide} />
          <ConversationRiskApprovalCard gate={secondGate} onDecide={onDecide} />
        </>,
      );
      const nextHeading = container.querySelectorAll<HTMLElement>(
        "[data-risk-approval-heading]",
      )[1];
      const scrollIntoView = vi.fn();
      const focus = vi.spyOn(nextHeading, "focus");
      nextHeading.scrollIntoView = scrollIntoView;

      await user.click(screen.getAllByRole("button", { name: buttonName })[0]);

      await waitFor(() => {
        expect(scrollIntoView).toHaveBeenCalledWith({
          behavior: "smooth",
          block: "start",
        });
      });
      expect(focus).toHaveBeenCalledWith({ preventScroll: true });
    },
  );

  it("does not advance when the decision fails", async () => {
    const user = userEvent.setup();
    const onDecide = vi.fn().mockRejectedValue(new Error("提交失败"));
    const firstGate = gate("pending");
    const secondGate = {
      ...gate("pending"),
      id: "gate-2",
      membership_hash: "membership-2",
    };
    const { container } = render(
      <>
        <ConversationRiskApprovalCard gate={firstGate} onDecide={onDecide} />
        <ConversationRiskApprovalCard gate={secondGate} onDecide={onDecide} />
      </>,
    );
    const nextHeading = container.querySelectorAll<HTMLElement>(
      "[data-risk-approval-heading]",
    )[1];
    const scrollIntoView = vi.fn();
    nextHeading.scrollIntoView = scrollIntoView;

    await user.click(
      screen.getAllByRole("button", { name: "同意高风险操作" })[0],
    );

    expect(await screen.findByText("提交失败")).toBeInTheDocument();
    expect(scrollIntoView).not.toHaveBeenCalled();
  });
});
