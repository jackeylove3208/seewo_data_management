import { beforeEach, describe, expect, it, vi } from "vitest";

import { agentApi } from "./agent";

describe("Agent API", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: vi.fn().mockResolvedValue({}),
    }));
  });

  it("uses typed conversation and start commands", async () => {
    await agentApi.createConversation();
    await agentApi.sendMessage("conversation-1", "同步全校教师");
    await agentApi.startTask("conversation-1", {
      title: "全校教师同步",
      entity_types: ["teacher"],
      source: { kind: "csv", upload_id: "source-1" },
      target: { kind: "csv", upload_id: "target-1" },
    }, "start-key");

    const calls = vi.mocked(fetch).mock.calls;
    expect(calls[0]?.[0]).toBe("/api/agent/conversations");
    expect(calls[1]?.[0]).toBe("/api/agent/conversations/conversation-1/messages");
    expect(calls[2]?.[0]).toBe("/api/agent/conversations/conversation-1/tasks");
    expect((calls[2]?.[1] as RequestInit).headers).toEqual(expect.objectContaining({ "Idempotency-Key": "start-key" }));
  });

  it("reads persisted task events and sends only control commands", async () => {
    await agentApi.events("task-1", "cursor-2");
    await agentApi.terminate("task-1");
    await agentApi.approveGroup?.("task-1", "group-1");

    const paths = vi.mocked(fetch).mock.calls.map(([path]) => path);
    expect(paths).toEqual([
      "/api/agent/tasks/task-1/events?cursor=cursor-2",
      "/api/agent/tasks/task-1/terminate",
      "/api/agent/tasks/task-1/approval-groups/group-1/approve",
    ]);
  });

  it("keeps rollback preview and human confirmation as separate commands", async () => {
    await agentApi.previewRollback("source-task-1");
    await agentApi.confirmRollback("rollback-task-1");
    await agentApi.rejectRollback("rollback-task-2");

    const calls = vi.mocked(fetch).mock.calls;
    expect(calls[0]?.[0]).toBe("/api/agent/tasks/source-task-1/rollback-preview");
    expect(calls[1]?.[0]).toBe("/api/agent/rollback-tasks/rollback-task-1/confirm");
    expect(calls[2]?.[0]).toBe("/api/agent/rollback-tasks/rollback-task-2/reject");
  });

  it("submits a frozen group of gate decisions in one request", async () => {
    await agentApi.decideGraphGates("task-1", [
      {
        gate_id: "gate-1",
        decision: "approve",
        approved_finding_ids: ["finding-1"],
        rejected_finding_ids: [],
        graph_cursor: 8,
        membership_hash: "a".repeat(64),
      },
      {
        gate_id: "gate-2",
        decision: "reject",
        approved_finding_ids: [],
        rejected_finding_ids: ["finding-2"],
        graph_cursor: 8,
        membership_hash: "b".repeat(64),
      },
    ]);

    const [path, request] = vi.mocked(fetch).mock.calls[0]!;
    expect(path).toBe("/api/agent/tasks/task-1/graph/gates/decisions");
    expect(JSON.parse(String((request as RequestInit).body))).toEqual({
      decisions: [
        expect.objectContaining({ gate_id: "gate-1", decision: "approve" }),
        expect.objectContaining({ gate_id: "gate-2", decision: "reject" }),
      ],
    });
  });
});
