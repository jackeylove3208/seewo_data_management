import { afterEach, describe, expect, it, vi } from "vitest";

import { reconciliationApi } from "./reconciliation";

describe("reconciliation API", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("uses bounded workflow commands and cursor filters", async () => {
    const fetchSpy = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({
      items: [],
      next_cursor: null,
    }), { status: 200, headers: { "Content-Type": "application/json" } })));
    vi.stubGlobal("fetch", fetchSpy);

    await reconciliationApi.advance("task-1");
    await reconciliationApi.retry("task-1");
    await reconciliationApi.listDifferences("task-1", {
      entity_type: "teacher",
      analysis_status: "manual_review",
      cursor: "next page",
    });

    expect(fetchSpy).toHaveBeenNthCalledWith(1, "/api/reconciliation-tasks/task-1/workflow/advance", expect.objectContaining({ method: "POST" }));
    expect(fetchSpy).toHaveBeenNthCalledWith(2, "/api/reconciliation-tasks/task-1/workflow/retry", expect.objectContaining({ method: "POST" }));
    expect(fetchSpy.mock.calls[2]?.[0]).toBe("/api/reconciliation-tasks/task-1/differences?entity_type=teacher&analysis_status=manual_review&cursor=next+page");
  });

  it("does not accept rewritten AI proposal content", async () => {
    const fetchSpy = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({}), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    })));
    vi.stubGlobal("fetch", fetchSpy);
    const body = {
      analysis_id: "analysis-1",
      option_id: "option-2",
      expected_difference_version: 3,
    };

    await reconciliationApi.previewAIProposal("difference-1", body);
    await reconciliationApi.confirmAIProposal("difference-1", body);

    const previewInit = fetchSpy.mock.calls[0]?.[1] as RequestInit;
    const confirmInit = fetchSpy.mock.calls[1]?.[1] as RequestInit;
    expect(JSON.parse(String(previewInit.body))).toEqual(body);
    expect(JSON.parse(String(confirmInit.body))).toEqual(body);
    expect(fetchSpy.mock.calls[0]?.[0]).toBe("/api/differences/difference-1/proposals/from-analysis/preview");
    expect(fetchSpy.mock.calls[1]?.[0]).toBe("/api/differences/difference-1/proposals/from-analysis");
  });
});
