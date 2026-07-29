# 回滚明细与重复进入交互设计

## 目标

修复两个回滚交互问题：

1. 回滚最终审批只显示操作数量，没有展示每一条待回滚数据。
2. 同一同步任务已经完成回滚后再次点击“回滚”，前端仍展示待确认弹窗；确认和取消都会调用不适用于已结束任务的接口，出现英文错误并停留在弹窗中。

## 已确认的业务规则

- 同一源同步任务和同一目标版本只允许一个回滚任务生命周期。
- 已经完成的回滚不能再次执行。再次点击时提示“该任务已完成回滚”，并允许查看已有回滚任务。
- 正在执行的回滚不能重复创建。再次点击时提示“回滚任务正在进行”，并允许查看已有回滚任务。
- “暂不回滚”是当前页面的临时取消，只关闭确认弹窗，不终止服务端创建的待确认回滚任务。
- 后续再次点击“回滚”时，可以继续确认同一个尚未执行的回滚任务。
- 新同步会产生新的源任务，因此不受旧任务已经完成回滚的限制。

## 根因

### 审批缺少明细

`rollback_approval` gate 只持久化原始操作 ID。任务进度 API 目前只为 `high_risk_approval` gate 构建 `items`，所以前端虽然复用了 `ApprovalItemDetails`，但回滚审批收到的 `items` 为空，只能显示总数。

### 重复进入卡在确认弹窗

回滚预览使用 `source_task_id + target_version_id` 作为幂等键。已有回滚任务时，服务端会正确返回原任务，但预览响应始终把它标记为 `requires_confirmation=true`。前端随后对已经完成的任务调用 confirm 或 reject：

- confirm 返回 “rollback Agent task is already confirmed”；
- reject 返回 “rollback Agent task is not awaiting confirmation”。

两个请求都失败，前端 catch 分支不会清空 `rollbackPreview`，因此弹窗无法退出。

## 方案

### 1. 回滚预览返回已有任务状态

扩展 `AgentRollbackPreviewResponse`：

- `state`：
  - `awaiting_confirmation`
  - `in_progress`
  - `completed`
  - `ended`
- `message_zh`：与状态对应的中文说明。
- `requires_confirmation`：只有任务仍处于 `intent_confirmed + pending` 时为 `true`。

`AgentReportingService.create_rollback_task` 在命中幂等任务时同时读取该任务的 run：

- `intent_confirmed + pending` → `awaiting_confirmation`
- `completed` → `completed`
- `running` 或 `waiting_human` → `in_progress`
- `terminated`、`failed` 或其他终态 → `ended`

新建的回滚预览返回 `awaiting_confirmation`。

不创建第二个回滚任务，也不改变现有幂等键。

### 2. 前端按预览状态展示不同弹窗

待确认任务：

- 标题：“确认创建独立回滚任务？”
- 主按钮：“确认回滚”
- 次按钮：“暂不回滚”
- “暂不回滚”只清空本地 `rollbackPreview`，不调用 reject API。

已有任务：

- `completed`：标题“该任务已完成回滚”
- `in_progress`：标题“回滚任务正在进行”
- `ended`：标题“已有回滚任务已结束”
- 主按钮：“查看回滚任务”，直接跳转已有任务详情。
- 次按钮：“关闭”，只关闭弹窗。
- 不调用 confirm 或 reject API。

API 失败时仍显示中文兜底错误，但正常的重复进入流程不再依赖捕获 409。

保留 reject API 兼容已有客户端；本页面不再把“暂不回滚”解释为服务端终止。

### 3. 回滚最终审批展示完整清单

任务进度 API 为 `rollback_approval` gate 构建与 `member_ids` 一一对应的 `items`。

新增回滚审批明细构建器，输入为：

- 当前回滚任务；
- gate 中冻结的原始操作 ID；
- 回滚任务 `agent_intent.operations` 中的执行事实；
- 原同步任务的治理操作、finding、work item 和目标输入记录。

每一项输出：

- 人员类型、姓名、编号、班级；
- 目标定位信息；
- 中文回滚动作；
- 原同步操作类型；
- 回滚字段变化。

变化方向必须是回滚方向：

- 原 update：同步后的值 → 同步前的值；
- 原 create：删除同步新增的记录；
- 原 delete：重新创建同步删除的记录。

数据展示继续使用现有脱敏函数，手机号等敏感字段不得以明文进入 API。

若旧事实缺少人员展示字段，仍必须为每个 gate member 生成一条安全兜底明细，使用实体类型和目标定位信息说明操作，确保“8 条操作”对应 8 条可查看记录。

前端继续复用 `ApprovalItemDetails`，默认折叠超过 3 条的清单，用户可以点击“查看具体操作（8 条）”展开。

## 数据流

```text
原任务点击回滚
  → POST rollback-preview
  → 按幂等键创建或读取已有回滚任务
  → 返回 state / message_zh / requires_confirmation
  → 前端选择“确认弹窗”或“已有任务提示”

回滚任务进入最终审批
  → rollback_approval gate 持有冻结操作 ID
  → 进度 API 从 agent_intent 和原治理事实构建回滚方向明细
  → human_gates[].items 返回逐项清单
  → 前端展开显示 8 条具体数据
```

## 错误与并发处理

- 幂等任务读取和状态分类都以服务端持久化状态为准。
- 即使前端轮询期间任务从待确认变为运行中，confirm 的状态冲突仍保留中文兜底提示，并刷新任务状态。
- 已有任务提示中的“查看回滚任务”不执行任何写操作。
- 关闭弹窗不改变服务端任务状态，不会产生额外回滚任务。
- 本修改不改变写入前数据重读、比较哈希或 SQL 乐观并发控制。

## 测试

后端：

- 回滚预览新建任务返回 `awaiting_confirmation`。
- 再次预览待确认任务仍返回同一任务且可以确认。
- 已完成回滚再次预览返回 `completed`、`requires_confirmation=false` 和中文说明。
- `rollback_approval` gate 返回与 member 数量一致的 items。
- update 明细按 after → before 展示。
- create/delete 明细使用正确的中文补偿动作。
- 缺少可选展示字段时仍生成安全兜底项。

前端：

- 回滚审批展示并可展开具体操作清单。
- 新预览点击“暂不回滚”只关闭弹窗，不调用 reject API。
- 已完成回滚显示中文提示，不调用 confirm/reject。
- “查看回滚任务”跳转到已有任务详情。
- 正在执行和已经结束状态显示对应中文提示。

质量门：

- 后端 pytest、Ruff、mypy。
- 前端 Vitest、ESLint、TypeScript、生产构建。
- OpenSpec 严格校验。

## 非目标

- 不允许同一同步任务创建多个已执行回滚。
- 不修改回滚数据安全判定算法。
- 不删除 reject API。
- 不新增数据库迁移。
