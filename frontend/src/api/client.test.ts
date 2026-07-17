import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, requestJson } from "./client";

describe("requestJson", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("explains when the backend proxy is unavailable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("proxy error", {
      status: 500,
      headers: { "Content-Type": "text/plain" },
    })));

    await expect(requestJson("/api/uploads")).rejects.toEqual(
      new ApiError("后端服务不可用，请确认本地服务已经启动后重试", 500),
    );
  });

  it("normalizes a network failure into the same actionable error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    await expect(requestJson("/api/uploads")).rejects.toEqual(
      new ApiError("后端服务不可用，请确认本地服务已经启动后重试", 0),
    );
  });
});
