# 测试验收问题改错清单

## 目的

修复 2026-07-27 项目验收中发现的测试基础设施与文档漂移问题，使发布质量门能够稳定反映当前产品行为。本清单只涉及测试、测试夹具和开发文档；现有证据未显示后端或前端业务逻辑回归。

## 验收基线

- 后端：906 个可离线执行的测试通过；真实大模型网关测试因未配置凭证未执行。
- 后端静态检查：Ruff、mypy 均通过。
- 前端：Vitest 152 项测试、ESLint、类型检查和生产构建均通过。
- PostgreSQL：全新迁移与任务删除迁移测试均通过。
- OpenSpec：13 个规范严格校验通过。
- Playwright：15 通过、13 失败、2 个按平台条件跳过；这是当前发布阻塞项。

## P1：修复 Agent 流程 Playwright 夹具

### 现象

`frontend/tests/e2e/agent-workflow.spec.ts` 的“会话 Agent 处理审批、澄清和终止”在桌面与移动端均失败；`agent-graph.spec.ts` 的高风险审批流程也失败。

### 根因

会话页面现在在初始化时调用 `GET /api/agent/conversations/current`，开始任务后还会并行请求事件和 `GET /api/agent/tasks/:taskId`。现有 E2E 只桩化了创建会话、发消息和事件接口，未桩化这两个新请求，导致代理到未启动的 `127.0.0.1:8000`。

高风险审批现在必须携带被冻结审批清单所需的 `graph_cursor`、`membership_hash` 和至少一条 `items`。现有图流程夹具只提供了 `item_count`，页面会按预期显示“审批清单缺少完整版本信息”。

### 修改

1. 在 `frontend/tests/e2e/agent-workflow.spec.ts` 为每个会话场景桩化：
   - `GET /api/agent/conversations/current`：首次进入返回 `null`，或返回与场景一致的持久化会话；
   - `GET /api/agent/tasks/task-1`：返回与事件阶段一致的任务状态。
2. 确保任务详情路由按 HTTP 方法区分，避免 `GET`、`DELETE` 与其他操作共用不兼容的响应。
3. 在 `frontend/tests/e2e/agent-graph.spec.ts` 的 `human_gates[0]` 中补齐冻结清单字段，至少包括 `membership_hash` 和可映射为 `finding_id` 的 `items`；字段形状以 `frontend/src/features/task-detail/AgentTaskDetailPage.test.tsx` 中的成功审批夹具为准。
4. 单独运行桌面和移动端场景，确认审批后页面显示“正在编译治理执行计划”，并且审批按钮消失。

### 验收

```bash
cd frontend
npm run test:e2e -- tests/e2e/agent-workflow.spec.ts --project=desktop --workers=1
npm run test:e2e -- tests/e2e/agent-graph.spec.ts --project=desktop --workers=1
npm run test:e2e -- tests/e2e/agent-workflow.spec.ts --project=mobile --workers=1
npm run test:e2e -- tests/e2e/agent-graph.spec.ts --project=mobile --workers=1
```

## P1：更新外部数据同步 E2E 场景

### 现象

`reconciliation-flow.spec.ts` 仍等待“选择希沃魔方 CSV”上传控件，导致“显示手动同步”和“创建同步任务”在桌面、移动端共四项失败。

### 根因

当前产品把目标端改为授权的本地 CSV：三方系统仍可上传 CSV 副本，而希沃目标必须从 `/api/agent/local-sources` 返回的可写本地源中选择。此变更已由组件单元测试覆盖，但 Playwright 未同步更新。

### 修改

1. 在 `frontend/tests/e2e/reconciliation-flow.spec.ts` 为手动同步场景桩化 `GET /api/agent/local-sources`，返回至少一个 `writable_as_target: true` 的本地源，例如 `seewo/current.csv`。
2. 将目标端断言和操作从 `选择希沃魔方 CSV` 文件上传改为选择 `希沃魔方本地 CSV` 下拉框中的授权源。
3. 保留三方系统 CSV 上传断言；只应删除目标端上传桩和目标文件上传动作。
4. 继续验证开始按钮的可用状态、重复点击只创建一次任务，以及页面跳转到新任务详情。

### 验收

```bash
cd frontend
npm run test:e2e -- tests/e2e/reconciliation-flow.spec.ts --project=desktop --workers=1 --grep "手动同步|创建同步任务"
npm run test:e2e -- tests/e2e/reconciliation-flow.spec.ts --project=mobile --workers=1 --grep "手动同步|创建同步任务"
```

## P2：同步过期的界面断言

### 报告标题

测试期望“数据同步报告”，当前页面的正式标题为“数据同步分析报告”。更新 `frontend/tests/e2e/agent-workflow.spec.ts` 的断言，保留对报告事实、排除项和执行结果的验证。

### 任务历史

`demo-001` 仍可用于单任务详情，但任务列表不再自动展示 demo 数据。为“打开历史任务”场景使用 `page.addInitScript` 写入 `mofa-reconciliation-tasks`，或改为完全桩化当前的 Agent 历史接口；不要依赖隐式默认数据。

### 移动端侧栏

侧栏只保留“新建对话”，不再有 `.workspace-new-task` 或“外部数据同步”链接。更新移动端测试：检查“新建对话”入口、抽屉开闭和跳转行为；外部数据同步应从任务列表页的按钮进入。

### 验收

```bash
cd frontend
npm run test:e2e -- tests/e2e/reconciliation-flow.spec.ts --project=desktop --workers=1
npm run test:e2e -- tests/e2e/reconciliation-flow.spec.ts --project=mobile --workers=1
```

## P2：修正开发质量门文档

### 现象

根目录 `AGENTS.md` 仍建议执行 `openspec validate basic-development`，但该变更已归档，因此命令必然报“Unknown item”。

### 修改

将命令替换为当前可用且更完整的严格校验：

```bash
openspec validate --all --strict --no-interactive
```

### 验收

```bash
openspec validate --all --strict --no-interactive
```

预期：13 个 spec 通过，0 个失败。

## 回归验收顺序

1. 先完成两项 P1，避免端到端门持续失效。
2. 处理 P2 的断言和文档漂移。
3. 运行完整前端质量门：

```bash
cd frontend
npm test -- --run
npm run lint
npm run typecheck
npm run build
npm run test:e2e
```

4. 运行后端质量门：

```bash
cd backend
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/mypy app
RECONCILIATION_MIGRATION_TEST_DATABASE_URL=postgresql+asyncpg://reconcile:reconcile@127.0.0.1:5432/reconcile_migration_test \
  .venv/bin/pytest tests/integration/test_migrations.py::test_clean_postgresql_migration_reaches_head -q
```

5. 最后执行：

```bash
cd ..
openspec validate --all --strict --no-interactive
git diff --check
```

## 完成标准

- `npm run test:e2e` 为 0 失败；
- 前端与后端既有质量门继续通过；
- OpenSpec 严格校验为 0 失败；
- 不引入真实组织数据、模型凭证或未脱敏日志；
- 若产品行为再次调整，同一提交必须同步更新对应 Vitest 和 Playwright 场景。

