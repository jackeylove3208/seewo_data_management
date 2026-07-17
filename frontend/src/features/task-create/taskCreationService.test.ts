import { describe, expect, it, vi } from "vitest";

import type { ReconciliationTaskResponse } from "../../api/ingestion";
import { createInitialDraft } from "./assistant";
import { createTaskFromDraft } from "./taskCreationService";

const csv = "entity_type,id,name\n教师,T01,张三\n";

describe("task creation service", () => {
  it("uploads both demo files and creates a partial task with one stable key", async () => {
    const task = {
      id: "task-001",
      tenant_id: "demo-school",
      scope_id: "七年级",
      status: "ready",
      stage: "analysis",
      entity_types: ["teacher"],
      snapshots: {
        authoritative: { accepted: 1, normalized_with_warning: 0, quarantined: 0, rejected: 0, quarantine_available: false },
        target: { accepted: 1, normalized_with_warning: 0, quarantined: 0, rejected: 0, quarantine_available: false },
      },
      workflow: {
        stage: "complete",
        status: "succeeded",
        attempt: 1,
        processed: 1,
        total: 1,
        analysis: { total: 1, completed: 1, succeeded: 1, manual_review: 0, failed: 0 },
        error: null,
      },
      error: null,
    } satisfies ReconciliationTaskResponse;
    const api = {
      upload: vi.fn()
        .mockResolvedValueOnce({ id: "source-upload" })
        .mockResolvedValueOnce({ id: "target-upload" }),
      createTask: vi.fn().mockResolvedValue(task),
    };
    const saveTask = vi.fn();
    const draft = {
      ...createInitialDraft(),
      title: "七年级教师核对",
      scopeLabel: "七年级",
      snapshotMode: "partial" as const,
      entityTypes: ["teacher" as const],
      source: { file: new File([csv], "third-party.csv", { type: "text/csv" }), summary: { total: 1, counts: { organization_unit: 0, class: 0, teacher: 1, student: 0 }, sample: [] } },
      target: { file: new File([csv], "mofa.csv", { type: "text/csv" }), summary: { total: 1, counts: { organization_unit: 0, class: 0, teacher: 1, student: 0 }, sample: [] } },
    };

    const result = await createTaskFromDraft(draft, "stable-key", { api, saveTask });

    expect(api.upload).toHaveBeenNthCalledWith(1, draft.source.file, "authoritative");
    expect(api.upload).toHaveBeenNthCalledWith(2, draft.target.file, "target");
    expect(api.createTask).toHaveBeenCalledWith(expect.objectContaining({
      authoritative_upload_id: "source-upload",
      target_upload_id: "target-upload",
      snapshot_mode: "partial",
      scope_id: "七年级",
    }), "stable-key");
    expect(api.createTask.mock.calls[0]?.[0]).not.toHaveProperty("tenant_id");
    expect(saveTask).toHaveBeenCalledWith(expect.objectContaining({ title: "七年级教师核对" }));
    expect(result.id).toBe("task-001");
  });
});
