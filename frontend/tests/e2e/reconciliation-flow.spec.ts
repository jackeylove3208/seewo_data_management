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

test("opens history, returns, and inspects one issue independently", async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem("mofa-reconciliation-tasks", JSON.stringify([{
      id: "demo-001",
      title: "三方全校数据核对",
      createdAt: "2026-07-16T10:32:00+08:00",
      sourceFile: "third_party_data.csv",
      targetFile: "mofa_data.csv",
      sourceAccepted: 515,
      targetAccepted: 518,
      issueCount: 9,
      status: "ready",
      selectedEntityTypes: ["organization_unit", "class", "teacher", "student"],
      isDemo: true,
    }]));
  });
  await page.route("**/api/agent/history*", (route) =>
    route.fulfill({
      status: 503,
      json: { detail: { code: "offline_fixture", message: "使用本地演示历史" } },
    }),
  );
  await page.route("**/api/reconciliation-tasks/demo-001", (route) =>
    route.fulfill({
      json: {
        id: "demo-001",
        tenant_id: "school-1",
        scope_id: "all",
        status: "ready",
        stage: "analysis_ready",
        entity_types: ["teacher"],
        snapshots: {
          authoritative: {
            accepted: 1,
            normalized_with_warning: 0,
            quarantined: 0,
            rejected: 0,
            quarantine_available: false,
          },
          target: {
            accepted: 1,
            normalized_with_warning: 0,
            quarantined: 0,
            rejected: 0,
            quarantine_available: false,
          },
        },
        workflow: {
          stage: "complete",
          status: "succeeded",
          attempt: 1,
          processed: 1,
          total: 1,
          analysis: {
            job_id: null,
            total: 1,
            completed: 1,
            succeeded: 1,
            manual_review: 0,
            failed: 0,
          },
          error: null,
        },
        error: null,
      },
    }),
  );
  await page.route(
    "**/api/reconciliation-tasks/demo-001/analysis-summary",
    (route) => route.fulfill({
      json: {
        task_id: "demo-001",
        analysis_job_id: null,
        job_status: "completed",
        terminal: true,
        entity_types: [{
          entity_type: "teacher",
          issue_count: 1,
          proposal_ready: 1,
          needs_information: 0,
          manual_only: 0,
          failed: 0,
        }],
      },
    }),
  );
  await seedGovernanceWorkbench(page, "ai", "demo-001", false);
  await page.goto("/tasks");
  const history = page.getByRole("region", { name: "历史任务" });
  await history.getByRole("button", { name: /^同步 三方全校数据核对 / }).click();
  await expect(page).toHaveURL(/\/tasks\/demo-001$/);

  await page.getByRole("button", { name: "返回任务列表" }).click();
  await expect(page).toHaveURL(/\/tasks$/);

  await history.getByRole("button", { name: /^同步 三方全校数据核对 / }).click();
  await page.getByRole("button", { name: "查看教师问题" }).click();
  await expect(page.getByRole("heading", { name: "教师差异" })).toBeVisible();
  await expect(page.getByText("张老师", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "查看 AI 分析" }).click();
  await expect(page.getByRole("dialog", { name: "差异治理分析" })).toBeVisible();
  await expect(page.getByText("希沃手机号未同步到权威系统最新值")).toBeVisible();
});

test("reveals only manual external data sync after explicit selection", async ({ page }) => {
  await page.route("**/api/agent/local-sources", (route) =>
    route.fulfill({
      json: [
        {
          source_ref: "seewo/current.csv",
          kind: "csv",
          writable_as_target: true,
        },
      ],
    }),
  );
  await page.goto("/tasks/new");

  await expect(page.getByRole("heading", { name: "外部数据同步" })).toBeVisible();
  await expect(page.getByText(/自动同步/)).toHaveCount(0);
  await expect(page.getByLabel("选择三方系统 CSV")).toHaveCount(0);
  await page.getByRole("button", { name: "手动同步" }).click();
  await expect(page.getByLabel("选择三方系统 CSV")).toBeVisible();
  const target = page.getByLabel("希沃魔方本地 CSV");
  await expect(target).toBeVisible();
  await expect(
    target.getByRole("option", { name: "seewo/current.csv" }),
  ).toBeAttached();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
    ),
  ).toBe(false);
});

test("keeps new conversation focused on agent chat", async ({ page }) => {
  await page.route("**/api/agent/history*", (route) => route.fulfill({ json: { items: [], next_cursor: null } }));
  await page.route("**/api/agent/conversations/current", (route) =>
    route.fulfill({ json: null }),
  );
  await page.route("**/api/agent/conversations", (route) => route.fulfill({ status: 201, json: { id: "conversation-focus", status: "active" } }));
  await page.route("**/api/agent/conversations/conversation-focus/messages", (route) => route.fulfill({ json: {
    message: "已记录七年级教师、学生同步需求。",
    intent: { title: "七年级师生同步", entity_types: ["teacher", "student"] },
  } }));
  await page.goto("/conversations/new");

  await expect(page.getByRole("heading", { name: "新建对话" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "开启新对话" })).toBeVisible();
  await page.getByLabel("对账目标").fill("只核对七年级的老师和学生");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText(/已记录.*同步需求/)).toBeVisible();
  await expect(page.getByText("任务草案", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "继续外部数据同步" })).toHaveCount(0);
  await expect(page).toHaveURL(/\/conversations\/new$/);
});

test("conversation workspace fills the viewport and keeps its composer visible", async ({ page }, testInfo) => {
  await page.route("**/api/agent/conversations/current", (route) =>
    route.fulfill({ json: null }),
  );
  await page.route("**/api/agent/conversations", (route) =>
    route.fulfill({
      status: 201,
      json: { id: "conversation-layout", status: "active" },
    }),
  );
  await page.goto("/conversations/new");

  const surface = await page.locator(".conversation-surface").boundingBox();
  const composer = await page.locator(".conversation-composer").boundingBox();
  expect(surface).not.toBeNull();
  expect(composer).not.toBeNull();
  expect(surface!.height).toBeGreaterThan(testInfo.project.name === "desktop" ? 520 : 380);
  if (testInfo.project.name === "desktop") {
    expect(composer!.y + composer!.height).toBeLessThanOrEqual(page.viewportSize()!.height + 1);
  } else {
    await page.locator(".conversation-composer").scrollIntoViewIfNeeded();
    const visibleComposer = await page.locator(".conversation-composer").boundingBox();
    expect(visibleComposer!.y + visibleComposer!.height).toBeLessThanOrEqual(page.viewportSize()!.height + 1);
  }
});

test("creates a task from independent manual external data sync", async ({ page }, testInfo) => {
  await page.route("**/health/ready", async (route) => route.fulfill({ json: { status: "ok" } }));
  let uploadCount = 0;
  let taskCreateCount = 0;
  let taskCreated = false;
  let taskRequest: Record<string, unknown> | undefined;
  let releaseTaskCreation!: () => void;
  const taskCreationGate = new Promise<void>((resolve) => {
    releaseTaskCreation = resolve;
  });
  await page.route("**/api/uploads", async (route) => {
    uploadCount += 1;
    await route.fulfill({
      status: 201,
      json: {
        id: "source-upload",
        source_role: "authoritative",
        original_name: "third-party.csv",
        size_bytes: csv.length,
        detected_encoding: "utf-8",
      },
    });
  });
  await page.route("**/api/agent/local-sources", (route) =>
    route.fulfill({
      json: [
        {
          source_ref: "seewo/current.csv",
          kind: "csv",
          writable_as_target: true,
        },
      ],
    }),
  );
  await page.route("**/api/agent/tasks", async (route) => {
    taskCreateCount += 1;
    taskRequest = route.request().postDataJSON();
    await taskCreationGate;
    taskCreated = true;
    await route.fulfill({
      status: 202,
      json: {
        id: "task-created",
        workflow_version: "new-agent-v1",
        task_kind: "sync",
        phase: "ingest_and_normalize",
        status: "running",
        title: "全校组织数据同步",
      },
    });
  });
  const createdTask = {
    id: "task-created",
    workflow_version: "new-agent-v1",
    task_kind: "sync",
    parent_task_id: null,
    phase: "ingest_and_normalize",
    status: "running",
    title: "全校组织数据同步",
    report_id: null,
    rollback_eligible: false,
    deletion_eligible: true,
  };
  await page.route("**/api/agent/history*", (route) => route.fulfill({ json: {
    items: taskCreated ? [{
      ...createdTask,
      created_at: "2026-07-23T10:00:00Z",
      completed_at: null,
      issue_summary: { total: 0, excluded: 0 },
      operation_summary: { succeeded: 0, failed: 0, blocked: 0 },
      entity_types: ["department", "student", "teacher"],
    }] : [],
    next_cursor: null,
  } }));
  await page.route("**/api/agent/tasks/task-created", (route) => route.fulfill({ json: createdTask }));
  await page.route("**/api/agent/tasks/task-created/events*", (route) => route.fulfill({ json: { cursor: "0", events: [] } }));

  await page.goto("/tasks/new");
  await expect(page.getByRole("heading", { name: "外部数据同步" })).toBeVisible();
  await expect(page.getByText(/自动同步/)).toHaveCount(0);
  await page.getByRole("button", { name: "手动同步" }).click();
  await expect(page.getByLabel("同步任务名称")).toHaveValue("全校组织数据同步");

  await page.getByLabel("选择三方系统 CSV").setInputFiles({ name: "third-party.csv", mimeType: "text/csv", buffer: csv });
  await page.getByLabel("希沃魔方本地 CSV").selectOption("seewo/current.csv");
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

  const startSync = page.getByRole("button", { name: "开始同步" });
  await startSync.click();
  await expect(startSync).toBeDisabled();
  await expect.poll(() => taskCreateCount).toBe(1);
  expect(uploadCount).toBe(1);
  expect(taskRequest).toMatchObject({
    source: { kind: "csv", upload_id: "source-upload" },
    target: { kind: "local", source_ref: "seewo/current.csv" },
  });
  await startSync.dispatchEvent("click");
  expect(taskCreateCount).toBe(1);
  releaseTaskCreation();

  await expect(page).toHaveURL(/\/tasks\/task-created$/);
  if (testInfo.project.name === "mobile") {
    await page.getByRole("button", { name: "打开导航" }).click();
  }
  await expect(page.getByRole("link", { name: /全校组织数据同步/ })).toHaveAttribute("aria-current", "page");
});

test("collapses the desktop workspace without hiding the main task", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop");
  await page.addInitScript(() => {
    window.localStorage.setItem("mofa-reconciliation-tasks", JSON.stringify([{
      id: "demo-001",
      title: "三方全校数据核对",
      createdAt: "2026-07-16T10:32:00+08:00",
      sourceFile: "third_party_data.csv",
      targetFile: "mofa_data.csv",
      sourceAccepted: 515,
      targetAccepted: 518,
      issueCount: 9,
      status: "ready",
      selectedEntityTypes: ["organization_unit", "class", "teacher", "student"],
      isDemo: true,
    }]));
  });
  await page.route("**/api/agent/history*", (route) =>
    route.fulfill({
      status: 503,
      json: { detail: { code: "offline_fixture", message: "使用本地演示历史" } },
    }),
  );
  await page.goto("/tasks/demo-001");

  await page.getByRole("button", { name: "收起侧栏" }).click();

  await expect(page.getByRole("button", { name: "展开侧栏" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "三方全校数据核对" })).toBeVisible();
});

test("uses a drawer for workspace navigation on mobile", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile");
  await page.addInitScript(() => localStorage.setItem("mofa-workspace-collapsed", "true"));
  await page.route("**/api/agent/history*", (route) =>
    route.fulfill({ json: { items: [], next_cursor: null } }),
  );
  await page.route("**/api/agent/conversations/current", (route) =>
    route.fulfill({ json: null }),
  );
  await page.route("**/api/agent/conversations", (route) =>
    route.fulfill({
      status: 201,
      json: { id: "conversation-mobile", status: "active" },
    }),
  );
  await page.goto("/tasks");

  await expect(page.getByRole("button", { name: "外部数据同步" })).toBeVisible();
  const sidebar = page.locator(".workspace-sidebar");
  await expect(sidebar).toHaveAttribute("aria-hidden", "true");
  await page.getByRole("button", { name: "打开导航" }).click();
  await expect(sidebar).not.toHaveAttribute("aria-hidden", "true");
  await expect(sidebar).toHaveClass(/is-mobile-open/);
  const commandBox = await page.locator(".workspace-agent-entry").boundingBox();
  expect(commandBox).not.toBeNull();
  expect(commandBox!.width).toBeGreaterThan(180);
  await expect(page.getByRole("link", { name: "新建对话" })).toHaveAttribute("href", "/conversations/new");
  await expect(page.getByRole("link", { name: "外部数据同步" })).toHaveCount(0);
  await page.getByRole("link", { name: "新建对话" }).click();

  await expect(page).toHaveURL(/\/conversations\/new$/);
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
  await page.emulateMedia({ reducedMotion: "reduce" });
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
  const workflow = { stage: "analysis", status: "running", attempt: 2, processed: 3, total: 5, analysis: { job_id: "job-progress", total: 5, completed: 3, succeeded: 2, manual_review: 1, failed: 0 }, error: null };
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
  await page.route("**/api/analysis-jobs/job-progress", async (route) => route.fulfill({ json: { job_id: "job-progress", task_id: taskId, status: "running", total: 5, completed: 3, succeeded: 2, manual_required: 1, needs_information: 1, manual_only: 0, failed: 0, proposal_ready: 2, last_error: null, updated_at: "2026-07-20T10:00:00Z" } }));
  await page.route(`**/api/reconciliation-tasks/${taskId}/differences*`, async (route) => route.fulfill({ json: { items: [], next_cursor: null } }));

  await page.goto(`/tasks/${taskId}`);
  await expect(page.getByText("AI 分析中")).toBeVisible();
  await expect(page.getByText("已完成 3 / 5")).toBeVisible();
  await expect(page.getByText(/待补信息 1/)).toBeVisible();
  await expect(page.getByText("问题类型对照")).toHaveCount(0);
  expect(await page.locator(".stage.active .spin").evaluate((element) => getComputedStyle(element).animationName)).toBe("none");
  const track = await page.getByRole("region", { name: "任务处理阶段" }).boundingBox().catch(() => null);
  if (track) expect(track.height).toBeGreaterThanOrEqual(100);
  await page.screenshot({ path: testInfo.outputPath("mandatory-analysis-progress.png"), fullPage: true });
});
