import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, requestJson } from "./client";

describe("requestJson", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("does not mislabel an internal backend error as service unavailable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("proxy error", {
      status: 500,
      headers: { "Content-Type": "text/plain" },
    })));

    await expect(requestJson("/api/uploads")).rejects.toEqual(
      new ApiError("后端处理请求失败，请查看后端终端日志后重试", 500),
    );
  });

  it("normalizes a network failure into the same actionable error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    await expect(requestJson("/api/uploads")).rejects.toEqual(
      new ApiError("后端服务不可用，请确认本地服务已经启动后重试", 0),
    );
  });

  it("preserves request cancellation semantics", async () => {
    const cancellation = new DOMException("The operation was aborted", "AbortError");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(cancellation));

    await expect(requestJson("/api/uploads")).rejects.toBe(cancellation);
  });

  it("accepts a successful response without a JSON body", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 204 })));

    await expect(requestJson<void>("/api/reconciliation-tasks/task-1", {
      method: "DELETE",
    })).resolves.toBeUndefined();
  });

  it("preserves a stable backend error code for UI recovery actions", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: {
        code: "conversation_active_task",
        message: "当前学校仍有任务正在处理",
      },
    }), {
      status: 409,
      headers: { "Content-Type": "application/json" },
    })));

    await expect(requestJson("/api/agent/conversations/current/reset")).rejects.toMatchObject({
      status: 409,
      code: "conversation_active_task",
      message: "当前学校仍有任务正在处理",
    });
  });
});
