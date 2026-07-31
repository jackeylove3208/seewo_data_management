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

  it("starts a conversation task with only the registered remote source id", async () => {
    await agentApi.startTask("conversation-1", {
      title: "远程学生同步",
      entity_types: ["student"],
      source: {
        kind: "remote_csv",
        remote_source_id: "remote-source-1",
        display_origin: "data.example.test",
      },
      target: { kind: "local", source_ref: "seewo/students.csv" },
    }, "remote-start-key");

    const [, request] = vi.mocked(fetch).mock.calls[0]!;
    expect(JSON.parse(String((request as RequestInit).body))).toEqual({
      title: "远程学生同步",
      entity_types: ["student"],
      source: {
        kind: "remote_csv",
        remote_source_id: "remote-source-1",
      },
      target: { kind: "local", source_ref: "seewo/students.csv" },
    });
  });

  it("sends explicit current-baseline acceptance only after user confirmation", async () => {
    await agentApi.startTask(
      "conversation-1",
      {
        title: "重新同步",
        entity_types: ["student"],
        source: { kind: "local", source_ref: "data/authority.csv" },
        target: { kind: "local", source_ref: "seewo/current.csv" },
      },
      "accepted-baseline-key",
      { acceptCurrentTargetBaseline: true },
    );

    const [, request] = vi.mocked(fetch).mock.calls[0]!;
    expect((request as RequestInit).headers).toEqual(expect.objectContaining({
      "X-Accept-Current-Target-Baseline": "true",
    }));
  });

  it("atomically resets the current conversation with an idempotency key", async () => {
    await agentApi.resetConversation("reset-key");

    const [path, request] = vi.mocked(fetch).mock.calls[0]!;
    expect(path).toBe("/api/agent/conversations/current/reset");
    expect((request as RequestInit).method).toBe("POST");
    expect((request as RequestInit).headers).toEqual(expect.objectContaining({
      "Idempotency-Key": "reset-key",
    }));
    expect(JSON.parse(String((request as RequestInit).body))).toEqual({});
  });

  it("keeps API credentials on the secure connector endpoints", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(new Response(JSON.stringify({
        id: "configuration-session-1",
        provider_id: "dingtalk",
        required_secret_fields: ["app_key", "app_secret"],
        expires_at: "2026-07-29T12:00:00Z",
      }), { status: 201 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        id: "connection-1",
        provider_id: "dingtalk",
        display_name: "学校钉钉",
        state: "pending",
        capabilities: {},
        visibility_summary: {},
      }), { status: 201 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        id: "connection-1",
        provider_id: "dingtalk",
        display_name: "学校钉钉",
        state: "active",
        capabilities: { "entity.teacher.read": true },
        visibility_summary: { visible: true, teacher_count: 5 },
      }), { status: 200 }));

    const result = await agentApi.configureApiConnection?.({
      conversation_id: "conversation-1",
      provider_id: "dingtalk",
      display_name: "学校钉钉",
      required_secret_fields: ["app_key", "app_secret"],
      public_configuration: {
        person_entity_kind: "student",
        root_department_id: 2,
        number_field: "student_number",
        class_name_field: "class_name",
      },
      secret: { app_key: "ding-app", app_secret: "ding-secret" },
    });

    const calls = vi.mocked(fetch).mock.calls;
    expect(calls.map(([path]) => path)).toEqual([
      "/api/connectors/configuration-sessions",
      "/api/connectors/connections",
      "/api/connectors/connections/connection-1/test",
    ]);
    expect(JSON.parse(String((calls[0]?.[1] as RequestInit).body))).toEqual({
      provider_id: "dingtalk",
      conversation_id: "conversation-1",
    });
    expect(JSON.parse(String((calls[1]?.[1] as RequestInit).body))).toEqual({
      configuration_session_id: "configuration-session-1",
      provider_id: "dingtalk",
      display_name: "学校钉钉",
      public_configuration: {
        person_entity_kind: "student",
        root_department_id: 2,
        number_field: "student_number",
        class_name_field: "class_name",
      },
      secret: { app_key: "ding-app", app_secret: "ding-secret" },
    });
    expect(JSON.parse(String((calls[2]?.[1] as RequestInit).body))).toEqual({
      conversation_id: "conversation-1",
    });
    expect(result?.state).toBe("active");
  });

  it("updates safe API configuration when retrying an existing connection", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(new Response(JSON.stringify({
        id: "connection-1",
        provider_id: "dingtalk",
        display_name: "学校钉钉",
        state: "pending",
        capabilities: {},
        visibility_summary: {},
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        id: "connection-1",
        provider_id: "dingtalk",
        display_name: "学校钉钉",
        state: "active",
        capabilities: { "entity.student.read": true },
        visibility_summary: { visible: true, student_count: 5 },
      }), { status: 200 }));

    await agentApi.configureApiConnection?.({
      provider_id: "dingtalk",
      display_name: "学校钉钉",
      required_secret_fields: ["app_key", "app_secret"],
      public_configuration: {
        person_entity_kind: "student",
        root_department_id: 2,
      },
      secret: { app_key: "ding-app", app_secret: "ding-secret" },
      connection_id: "connection-1",
    });

    const calls = vi.mocked(fetch).mock.calls;
    expect(calls.map(([path]) => path)).toEqual([
      "/api/connectors/connections/connection-1/rotate-secret",
      "/api/connectors/connections/connection-1/test",
    ]);
    expect(JSON.parse(String((calls[0]?.[1] as RequestInit).body))).toEqual({
      public_configuration: {
        person_entity_kind: "student",
        root_department_id: 2,
      },
      secret: { app_key: "ding-app", app_secret: "ding-secret" },
    });
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
