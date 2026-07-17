import { expect, test } from "@playwright/test";

const csv = Buffer.from("entity_type,id,name\n教师,T01,张三\n学生,S01,李四\n");

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

test("creates a task from an explicit conversational draft", async ({ page }, testInfo) => {
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
  await page.getByRole("textbox", { name: "对账要求" }).fill("只核对七年级的老师和学生");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByLabel("核对范围")).toHaveValue("七年级");

  await page.getByLabel("选择三方系统 CSV").setInputFiles({ name: "third-party.csv", mimeType: "text/csv", buffer: csv });
  await page.getByLabel("选择希沃魔方 CSV").setInputFiles({ name: "mofa.csv", mimeType: "text/csv", buffer: csv });
  await expect(page.getByRole("button", { name: "创建对账" })).toBeEnabled();
  await page.getByRole("button", { name: "创建对账" }).click();

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
  await page.goto("/tasks");

  const sidebar = page.locator(".workspace-sidebar");
  await expect(sidebar).toHaveAttribute("aria-hidden", "true");
  await page.getByRole("button", { name: "打开导航" }).click();
  await expect(sidebar).not.toHaveAttribute("aria-hidden", "true");
  await expect(sidebar).toHaveClass(/is-mobile-open/);
  await page.getByRole("link", { name: /三方全校数据核对，已完成，9 个问题/ }).click();

  await expect(page).toHaveURL(/\/tasks\/demo-001$/);
  await expect(sidebar).not.toHaveClass(/is-mobile-open/);
  await expect(sidebar).toHaveAttribute("aria-hidden", "true");
});
