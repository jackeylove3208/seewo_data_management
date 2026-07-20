import { expect, test } from "@playwright/test";

const csv = Buffer.from("entity_type,id,name\n教师,T01,张三\n学生,S01,李四\n");

async function seedGovernanceWorkbench(page, mode: "ai" | "manual", configuredTaskId?: string, addStoredTask = true) {
  const taskId = configuredTaskId ?? `real-${mode}`;
  const differenceId = `difference-${mode}`;
  if (addStoredTask) await page.addInitScript(({ taskId }) => {
    window.localStorage.setItem("mofa-reconciliation-tasks", JSON.stringify([{
      id: taskId,
      title: "教师手机号治理",
      createdAt: "2026-07-17T10:00:00Z",
      sourceFile: "third_party.csv",
      targetFile: "seewo.csv",
      sourceAccepted: 1,
      targetAccepted: 1,
      issueCount: 1,
      status: "ready",
      selectedEntityTypes: ["teacher"],
    }]));
  }, { taskId });
  const difference = {
    id: differenceId,
    task_id: taskId,
    tenant_id: "school-1",
    entity_type: "teacher",
    difference_type: "attribute_conflict",
    proposed_action: "update",
    evidence: {
      source_snapshot_id: "source",
      target_snapshot_id: "target",
      source_entity_id: "source-person",
      target_entity_id: "target-person",
      mapping_id: "mapping",
      fields: [{ field: "phone", source_value: "13800000000", target_value: "13900000000", normalized_source: "13800000000", normalized_target: "13900000000", comparison: "attribute" }],
      match_evidence: [],
      source_payload: { name: "张老师", phone: "13800000000" },
      target_payload: { name: "张老师", phone: "13900000000" },
      related_entities: [],
      comparison_rule_version: "comparison-v1",
    },
    status: "open",
    version: 1,
    created_at: "2026-07-17T10:00:00Z",
    analysis_status: mode === "manual" ? "manual_review" : "succeeded",
    risk: mode === "manual" ? "high" : "low",
    execution_eligible: mode === "ai",
    proposal_status: null,
    current_proposal_version: null,
  };
  await page.route(`**/api/reconciliation-tasks/${taskId}/differences*`, async (route) => route.fulfill({ json: { items: [difference], next_cursor: null } }));
  await page.route(`**/api/differences/${differenceId}/analysis`, async (route) => route.fulfill({ json: {
    id: `analysis-${mode}`,
    difference_id: differenceId,
    difference_version: 1,
    analysis_version: "analysis-v2",
    status: mode === "manual" ? "manual_review" : "succeeded",
    output: mode === "manual" ? {
      cause: "候选人员身份无法唯一确认",
      evidence_summary: "两条候选记录具有相同匹配分数",
      manual_only: true,
      manual_reason: "信息不足且变更风险较高，需要人工核实",
      options: [],
    } : {
      cause: "希沃手机号未同步到权威系统最新值",
      evidence_summary: "字段证据和快照时间支持更新",
      manual_only: false,
      manual_reason: null,
      options: [{
        option_id: "option-1",
        operation_type: "update",
        target_entity_id: "target-person",
        proposed_changes: [{ field: "phone", before: "13900000000", after: "13800000000" }],
        rationale: "采用权威系统的最新手机号",
        evidence_refs: ["field:phone"],
        risk: "low",
        confidence: 0.96,
        preconditions: ["目标记录版本保持不变"],
        recommended: true,
      }],
    },
    failure_code: null,
    attempt_count: 1,
    provenance: { provider: "enterprise-gateway", model: "enterprise-model", skill_name: "analyze-data-difference", skill_version: "1.0.0", prompt_version: "analysis-prompt-v2", tool_trace_ids: [], gateway_request_ids: ["request-1"], usage: { input_tokens: 10, output_tokens: 20 }, generated_at: "2026-07-17T10:00:00Z" },
  } }));
  await page.route("**/api/entity-editor-schemas/teacher", async (route) => route.fulfill({ json: { entity_type: "teacher", fields: [{ name: "phone", label: "手机号", field_type: "phone", required: false }, { name: "email", label: "邮箱", field_type: "email", required: false }] } }));
  await page.route(`**/api/differences/${differenceId}/proposals/**`, async (route) => {
    const source = route.request().url().includes("from-analysis") ? "ai" : "operator";
    const isPreview = route.request().url().endsWith("/preview");
    const request = route.request().postDataJSON();
    const changes = source === "ai" ? [{ field: "phone", before: "13900000000", after: "13800000000" }] : [{ field: "phone", before: "13900000000", after: request.changes.phone }];
    const preview = { difference_id: differenceId, difference_version: 1, proposal_source: source, operation_type: "update", target_entity_id: "target-person", changes, rationale: source === "ai" ? "采用权威系统的最新手机号" : request.rationale, evidence_refs: ["field:phone"], risk: source === "ai" ? "low" : "medium" };
    await route.fulfill({ status: isPreview ? 200 : 201, json: isPreview ? preview : { ...preview, id: `proposal-${mode}`, task_id: taskId, tenant_id: "school-1", analysis_id: `analysis-${mode}`, analysis_version: "analysis-v2", proposal_version: 1, created_by: "operator-1", created_at: "2026-07-17T10:01:00Z", status: "pending_execution", supersedes_id: null } });
  });
  return { taskId, differenceId };
}

test("opens history, returns, and selects one issue independently", async ({ page }, testInfo) => {
  await page.goto("/tasks");
  await page.getByRole("button", { name: /三方全校数据核对/ }).click();
  await expect(page).toHaveURL(/\/tasks\/demo-001$/);

  await page.getByRole("button", { name: "返回任务列表" }).click();
  await expect(page).toHaveURL(/\/tasks$/);

  await page.getByRole("button", { name: /三方全校数据核对/ }).click();
  await page.getByRole("button", { name: "查看教师问题" }).click();
  if (testInfo.project.name === "desktop") {
    const sidebarBox = await page.locator(".workspace-sidebar").boundingBox();
    const selectionBox = await page.locator(".selection-bar").boundingBox();
    expect(selectionBox?.x ?? 0).toBeGreaterThanOrEqual((sidebarBox?.width ?? 0) + 20);
  }
  await page.getByText("张三", { exact: true }).click();
  await page.getByLabel("选择张三的所属部门").check();

  await expect(page.getByText("已选择 1 人，共 1 个问题", { exact: true })).toBeVisible();
  await expect(page.getByLabel("选择张三的手机号")).not.toBeChecked();
});

test("creates a task from manual external data sync", async ({ page }, testInfo) => {
  await page.route("**/health/ready", async (route) => route.fulfill({ json: { status: "ok" } }));
  let uploadCount = 0;
  await page.route("**/api/uploads", async (route) => {
    uploadCount += 1;
    await route.fulfill({
      status: 201,
      json: {
        id: uploadCount === 1 ? "source-upload" : "target-upload",
        source_role: uploadCount === 1 ? "authoritative" : "target",
        original_name: uploadCount === 1 ? "third-party.csv" : "mofa.csv",
        size_bytes: csv.length,
        detected_encoding: "utf-8",
      },
    });
  });
  await page.route("**/api/reconciliation-tasks", async (route) => route.fulfill({
    status: 202,
    json: {
      id: "task-created",
      tenant_id: "demo-school",
      scope_id: "七年级",
      snapshot_mode: "partial",
      status: "ready",
      stage: "analysis",
      entity_types: ["teacher", "student"],
      snapshots: {
        authoritative: { accepted: 2, normalized_with_warning: 0, quarantined: 0, rejected: 0, quarantine_available: false },
        target: { accepted: 2, normalized_with_warning: 0, quarantined: 0, rejected: 0, quarantine_available: false },
      },
      error: null,
    },
  }));

  await page.goto("/tasks/new");
  await expect(page.getByRole("heading", { name: "外部数据同步" })).toBeVisible();
  await expect(page.locator(".sync-method-grid")).toHaveCSS("display", "grid");
  await expect(page.getByLabel("选择三方系统 CSV")).toHaveCount(0);
  await page.getByRole("button", { name: "手动同步" }).click();
  await page.getByLabel("同步任务名称").fill("七年级教师、学生核对");
  await page.getByLabel("核对范围").fill("七年级");
  await page.getByRole("button", { name: "指定范围" }).click();
  await page.getByLabel("部门").uncheck();
  await page.getByLabel("班级").uncheck();

  await page.getByLabel("选择三方系统 CSV").setInputFiles({ name: "third-party.csv", mimeType: "text/csv", buffer: csv });
  await page.getByLabel("选择希沃魔方 CSV").setInputFiles({ name: "mofa.csv", mimeType: "text/csv", buffer: csv });
  await expect(page.getByRole("button", { name: "开始同步" })).toBeEnabled();

  const viewportWidth = page.viewportSize()!.width;
  if (testInfo.project.name === "desktop") {
    const formBox = await page.locator(".manual-sync-form").boundingBox();
    expect(formBox).not.toBeNull();
    expect(formBox!.x).toBeGreaterThanOrEqual(0);
    expect(formBox!.x + formBox!.width).toBeLessThanOrEqual(viewportWidth + 1);
  } else {
    for (const locator of [page.locator(".sync-method"), page.locator(".sync-attachment")]) {
      const boxes = await locator.evaluateAll((elements) => elements.map((element) => {
        const rect = element.getBoundingClientRect();
        return { left: rect.left, right: rect.right };
      }));
      expect(boxes.length).toBeGreaterThan(0);
      for (const box of boxes) {
        expect(box.left).toBeGreaterThanOrEqual(0);
        expect(box.right).toBeLessThanOrEqual(viewportWidth + 1);
      }
    }
  }

  await page.getByRole("button", { name: "开始同步" }).click();

  await expect(page).toHaveURL(/\/tasks\/task-created$/);
  if (testInfo.project.name === "mobile") {
    await page.getByRole("button", { name: "打开导航" }).click();
  }
  await expect(page.getByRole("link", { name: /七年级教师、学生核对/ })).toHaveAttribute("aria-current", "page");
});

test("collapses the desktop workspace without hiding the main task", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop");
  await page.goto("/tasks/demo-001");

  await page.getByRole("button", { name: "收起侧栏" }).click();

  await expect(page.getByRole("button", { name: "展开侧栏" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "三方全校数据核对" })).toBeVisible();
});

test("uses a drawer for workspace navigation on mobile", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile");
  await page.addInitScript(() => localStorage.setItem("mofa-workspace-collapsed", "true"));
  await page.goto("/tasks");

  const sidebar = page.locator(".workspace-sidebar");
  await expect(sidebar).toHaveAttribute("aria-hidden", "true");
  await page.getByRole("button", { name: "打开导航" }).click();
  await expect(sidebar).not.toHaveAttribute("aria-hidden", "true");
  await expect(sidebar).toHaveClass(/is-mobile-open/);
  for (const command of [page.locator(".workspace-agent-entry"), page.locator(".workspace-new-task")]) {
    const commandBox = await command.boundingBox();
    expect(commandBox).not.toBeNull();
    expect(commandBox!.width).toBeGreaterThan(180);
  }
  await page.getByRole("link", { name: /三方全校数据核对，已完成，9 个问题/ }).click();

  await expect(page).toHaveURL(/\/tasks\/demo-001$/);
  await expect(sidebar).not.toHaveClass(/is-mobile-open/);
  await expect(sidebar).toHaveAttribute("aria-hidden", "true");
});

test("creates a pending proposal from a validated AI option", async ({ page }, testInfo) => {
  const { taskId } = await seedGovernanceWorkbench(page, "ai");
  await page.goto(`/tasks/${taskId}/differences/teacher`);
  await page.screenshot({ path: testInfo.outputPath("real-difference-list.png"), fullPage: true });
  await page.getByRole("button", { name: "查看 AI 分析" }).click();
  await expect(page.getByText("希沃手机号未同步到权威系统最新值")).toBeVisible();
  await page.getByRole("button", { name: "采用并预览" }).click();
  await expect(page.getByText("方案修改预览")).toBeVisible();
  const modalBox = await page.getByRole("dialog").boundingBox();
  expect(modalBox?.x ?? -1).toBeGreaterThanOrEqual(0);
  expect((modalBox?.x ?? 0) + (modalBox?.width ?? 0)).toBeLessThanOrEqual(page.viewportSize()!.width + 1);
  await page.screenshot({ path: testInfo.outputPath("ai-option-preview.png"), fullPage: true });
  await page.getByRole("button", { name: "确认生成待执行方案" }).click();
  await expect(page.getByText("已进入待治理执行")).toBeVisible();
});

test("creates a manual pending proposal when AI is manual-only", async ({ page }, testInfo) => {
  const { taskId } = await seedGovernanceWorkbench(page, "manual");
  let targetMutationCalls = 0;
  await page.route("**/api/**/target/**", async (route) => {
    targetMutationCalls += 1;
    await route.abort();
  });
  await page.goto(`/tasks/${taskId}/differences/teacher`);
  await page.getByRole("button", { name: "查看 AI 分析" }).click();
  await expect(page.getByText("信息不足且变更风险较高，需要人工核实")).toBeVisible();
  await expect(page.getByRole("button", { name: "采用并预览" })).toHaveCount(0);
  await page.getByRole("button", { name: "人工修改" }).click();
  await page.getByRole("textbox", { name: "手机号" }).fill("13700000000");
  await page.getByRole("textbox", { name: "修改原因" }).fill("校内通讯录人工核验通过");
  await page.getByRole("button", { name: "预览人工方案" }).click();
  await page.screenshot({ path: testInfo.outputPath("manual-proposal-preview.png"), fullPage: true });
  await page.getByRole("button", { name: "确认生成待执行方案" }).click();
  await expect(page.getByText("已进入待治理执行")).toBeVisible();
  expect(targetMutationCalls).toBe(0);
});

test("shows persisted mandatory analysis progress without layout shifts", async ({ page }, testInfo) => {
  const taskId = "real-progress";
  await page.addInitScript(({ taskId }) => {
    window.localStorage.setItem("mofa-reconciliation-tasks", JSON.stringify([{
      id: taskId,
      title: "全校教师数据核对",
      createdAt: "2026-07-17T10:00:00Z",
      sourceFile: "third_party.csv",
      targetFile: "seewo.csv",
      sourceAccepted: 80,
      targetAccepted: 82,
      issueCount: 5,
      status: "processing",
      selectedEntityTypes: ["teacher"],
    }]));
  }, { taskId });
  const workflow = { stage: "analysis", status: "pending", attempt: 2, processed: 3, total: 5, analysis: { total: 5, completed: 3, succeeded: 2, manual_review: 1, failed: 0 }, error: null };
  await page.route(`**/api/reconciliation-tasks/${taskId}`, async (route) => route.fulfill({ json: {
    id: taskId,
    tenant_id: "school-1",
    scope_id: "all",
    snapshot_mode: "full",
    entity_types: ["teacher"],
    status: "ready",
    stage: "differences_ready",
    snapshots: { authoritative: { accepted: 80, normalized_with_warning: 0, quarantined: 0, rejected: 0, quarantine_available: false }, target: { accepted: 82, normalized_with_warning: 0, quarantined: 0, rejected: 0, quarantine_available: false } },
    workflow,
    error: null,
  } }));
  await page.route(`**/api/reconciliation-tasks/${taskId}/workflow/advance`, async (route) => route.fulfill({ json: { task_id: taskId, workflow } }));
  await page.route(`**/api/reconciliation-tasks/${taskId}/differences*`, async (route) => route.fulfill({ json: { items: [], next_cursor: null } }));

  await page.goto(`/tasks/${taskId}`);
  await expect(page.getByText("AI 分析中")).toBeVisible();
  await expect(page.getByText("已完成 3 / 5")).toBeVisible();
  const track = await page.getByRole("region", { name: "任务处理阶段" }).boundingBox().catch(() => null);
  if (track) expect(track.height).toBeGreaterThanOrEqual(100);
  await page.screenshot({ path: testInfo.outputPath("mandatory-analysis-progress.png"), fullPage: true });
});

test("runs from synthetic CSV upload through automatic analysis to an AI proposal", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop");
  const taskId = "task-full-chain";
  await seedGovernanceWorkbench(page, "ai", taskId, false);
  let uploadCount = 0;
  await page.route("**/api/uploads", async (route) => {
    uploadCount += 1;
    await route.fulfill({ status: 201, json: { id: uploadCount === 1 ? "source-full" : "target-full", source_role: uploadCount === 1 ? "authoritative" : "target", original_name: uploadCount === 1 ? "third-party.csv" : "seewo.csv", size_bytes: csv.length, detected_encoding: "utf-8" } });
  });
  let workflow = { stage: "matching", status: "pending", attempt: 0, processed: 0, total: 0, analysis: { total: 0, completed: 0, succeeded: 0, manual_review: 0, failed: 0 }, error: null };
  const taskResponse = () => ({ id: taskId, tenant_id: "school-1", scope_id: "all", snapshot_mode: "full", entity_types: ["teacher"], status: "ready", stage: workflow.stage === "complete" ? "analysis_ready" : "snapshots", snapshots: { authoritative: { accepted: 2, normalized_with_warning: 0, quarantined: 0, rejected: 0, quarantine_available: false }, target: { accepted: 2, normalized_with_warning: 0, quarantined: 0, rejected: 0, quarantine_available: false } }, workflow, error: null });
  await page.route("**/api/reconciliation-tasks", async (route) => route.fulfill({ status: 202, json: taskResponse() }));
  await page.route(`**/api/reconciliation-tasks/${taskId}`, async (route) => route.fulfill({ json: taskResponse() }));
  let advanceCount = 0;
  await page.route(`**/api/reconciliation-tasks/${taskId}/workflow/advance`, async (route) => {
    advanceCount += 1;
    workflow = advanceCount === 1
      ? { ...workflow, stage: "differences", attempt: 1 }
      : advanceCount === 2
        ? { ...workflow, stage: "analysis", attempt: 1 }
        : { stage: "complete", status: "succeeded", attempt: 1, processed: 1, total: 1, analysis: { total: 1, completed: 1, succeeded: 1, manual_review: 0, failed: 0 }, error: null };
    await route.fulfill({ json: { task_id: taskId, workflow } });
  });

  await page.goto("/tasks/new");
  await page.getByLabel("选择三方系统 CSV").setInputFiles({ name: "third-party.csv", mimeType: "text/csv", buffer: csv });
  await page.getByLabel("选择希沃魔方 CSV").setInputFiles({ name: "seewo.csv", mimeType: "text/csv", buffer: csv });
  await page.getByRole("button", { name: "创建对账" }).click();
  await expect(page).toHaveURL(new RegExp(`/tasks/${taskId}$`));
  await expect(page.getByText("分析完成")).toBeVisible();
  await page.getByRole("button", { name: "查看教师问题" }).click();
  await page.getByRole("button", { name: "查看 AI 分析" }).click();
  await page.getByRole("button", { name: "采用并预览" }).click();
  await page.getByRole("button", { name: "确认生成待执行方案" }).click();
  await expect(page.getByText("已进入待治理执行")).toBeVisible();
  expect(advanceCount).toBe(3);
});
