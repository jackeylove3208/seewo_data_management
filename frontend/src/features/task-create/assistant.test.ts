import { describe, expect, it } from "vitest";

import { createEmptyTaskIntentDraft, createInitialDraft, deterministicTaskAssistant } from "./assistant";

describe("deterministic task assistant", () => {
  it("extracts selected people types and partial scope from a request", async () => {
    const response = await deterministicTaskAssistant.respond({
      draft: createInitialDraft(),
      message: "只核对七年级的老师和学生",
    });

    expect(response.patch.entityTypes).toEqual(["teacher", "student"]);
    expect(response.patch.snapshotMode).toBe("partial");
    expect(response.patch.scopeLabel).toBe("七年级");
    expect(response.message).toContain("草案已更新");
    expect(response.message).not.toContain("两份数据");
  });

  it("refuses direct governance and rollback commands", async () => {
    const response = await deterministicTaskAssistant.respond({
      draft: createInitialDraft(),
      message: "直接修复全部问题并回退上次操作",
    });

    expect(response.kind).toBe("guardrail");
    expect(response.message).toContain("不能直接执行");
  });

  it("asks for entity types when only a scope was recognized", async () => {
    const response = await deterministicTaskAssistant.respond({
      draft: createEmptyTaskIntentDraft(),
      message: "核对高中部",
    });

    expect(response.patch.scopeLabel).toBe("高中部");
    expect(response.message).toContain("实体类型");
  });

  it("asks for scope when only entity types were recognized", async () => {
    const response = await deterministicTaskAssistant.respond({
      draft: createEmptyTaskIntentDraft(),
      message: "核对老师和学生",
    });

    expect(response.patch.entityTypes).toEqual(["teacher", "student"]);
    expect(response.message).toContain("核对范围");
  });
});
