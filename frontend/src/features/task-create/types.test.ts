import { describe, expect, it } from "vitest";

import type { TaskIntentDraft } from "./types";
import { isTaskIntentReady } from "./types";

const validIntent: TaskIntentDraft = {
  title: "七年级教师核对",
  scopeLabel: "七年级",
  snapshotMode: "partial",
  entityTypes: ["teacher"],
};

describe("task intent validation", () => {
  it("accepts complete non-file task information", () => {
    expect(isTaskIntentReady(validIntent)).toBe(true);
  });

  it("rejects missing task information without requiring files", () => {
    expect(isTaskIntentReady({ ...validIntent, title: " " })).toBe(false);
    expect(isTaskIntentReady({ ...validIntent, scopeLabel: "" })).toBe(false);
    expect(isTaskIntentReady({ ...validIntent, entityTypes: [] })).toBe(false);
  });
});
