import { beforeEach, describe, expect, it } from "vitest";

import type { TaskIntentDraft } from "./types";
import {
  clearTaskIntentDraft,
  loadTaskIntentDraft,
  saveTaskIntentDraft,
  TASK_INTENT_STORAGE_KEY,
} from "./draftHandoff";

const intent: TaskIntentDraft = {
  title: "七年级教师核对",
  scopeLabel: "七年级",
  snapshotMode: "partial",
  entityTypes: ["teacher"],
};

describe("task intent handoff", () => {
  beforeEach(() => sessionStorage.clear());

  it("persists a versioned non-file draft for the current browser session", () => {
    expect(saveTaskIntentDraft(intent)).toBe(true);

    expect(loadTaskIntentDraft()).toEqual(intent);
    expect(JSON.parse(sessionStorage.getItem(TASK_INTENT_STORAGE_KEY) ?? "{}")).toEqual({
      version: 1,
      draft: intent,
    });
    expect(sessionStorage.getItem(TASK_INTENT_STORAGE_KEY)).not.toContain("source");
    expect(sessionStorage.getItem(TASK_INTENT_STORAGE_KEY)).not.toContain("target");
  });

  it("rejects incomplete drafts", () => {
    expect(saveTaskIntentDraft({ ...intent, entityTypes: [] })).toBe(false);
    expect(sessionStorage.getItem(TASK_INTENT_STORAGE_KEY)).toBeNull();
  });

  it("discards invalid or unsupported stored payloads", () => {
    sessionStorage.setItem(TASK_INTENT_STORAGE_KEY, JSON.stringify({
      version: 2,
      draft: intent,
    }));

    expect(loadTaskIntentDraft()).toBeUndefined();
    expect(sessionStorage.getItem(TASK_INTENT_STORAGE_KEY)).toBeNull();

    sessionStorage.setItem(TASK_INTENT_STORAGE_KEY, "not-json");
    expect(loadTaskIntentDraft()).toBeUndefined();
    expect(sessionStorage.getItem(TASK_INTENT_STORAGE_KEY)).toBeNull();
  });

  it("strips unexpected file-backed fields from a stored intent", () => {
    sessionStorage.setItem(TASK_INTENT_STORAGE_KEY, JSON.stringify({
      version: 1,
      draft: {
        ...intent,
        source: { summary: { total: 10 } },
        target: { summary: { total: 10 } },
      },
    }));

    expect(loadTaskIntentDraft()).toEqual(intent);
  });

  it("clears a handed-off draft explicitly", () => {
    saveTaskIntentDraft(intent);

    clearTaskIntentDraft();

    expect(loadTaskIntentDraft()).toBeUndefined();
  });
});
