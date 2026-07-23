# Apple 前端配合说明

这次前端改造不要求后端立即新增接口。第一版使用已有 Agent API，并明确区分“当前加载数据”和“完整时间范围统计”。前端不会根据不存在的字段生成“本周治理进度”等数字。入口层面仅保留任务主页右上角的“外部数据同步”，左侧导航不再重复提供该入口；路由和后端能力不变。

## 当前已使用的接口

### `GET /api/agent/history`

前端使用以下字段：

```json
{
  "items": [
    {
      "id": "task-id",
      "title": "全校组织数据同步",
      "status": "completed",
      "created_at": "2026-07-23T08:00:00Z",
      "issue_summary": { "total": 12, "excluded": 2 },
      "operation_summary": { "succeeded": 10, "failed": 1, "blocked": 1 },
      "rollback_eligible": true,
      "deletion_eligible": true
    }
  ],
  "next_cursor": null
}
```

前端当前展示规则：

- 历史任务：当前已加载的 `items.length`。
- 已完成：`status` 为 `completed` 或 `terminated` 的任务数。
- 待处理问题：所有已加载任务的 `issue_summary.total` 之和。
- 治理操作成功率：`succeeded / (succeeded + failed + blocked)`；没有操作记录时展示“暂无数据”。
- 页面文案必须写明“当前加载历史”，不能把游标分页结果称为“本周”或“全部历史”。

如果要提供严格的时间范围指标，建议新增：

```http
GET /api/agent/metrics?from=2026-07-20T00:00:00Z&to=2026-07-27T00:00:00Z
```

建议响应：

```json
{
  "from": "2026-07-20T00:00:00Z",
  "to": "2026-07-27T00:00:00Z",
  "task_count": 18,
  "completed_count": 14,
  "issue_count": 83,
  "operations": { "succeeded": 76, "failed": 3, "blocked": 4 },
  "operation_success_rate": 0.9157,
  "source": "tenant_agent_tasks"
}
```

后端必须以服务端时间和租户权限计算范围，不能让前端通过多页拼接冒充完整统计。

## 新建对话

当前前端继续使用：

- `POST /api/agent/conversations`
- `POST /api/agent/conversations/{conversation_id}/messages`
- `POST /api/agent/conversations/{conversation_id}/tasks`
- `GET /api/agent/tasks/{task_id}/events`
- `POST /api/agent/tasks/{task_id}/terminate`

事件仍需保持 `phase`、`status`、`type`、`payload`、`created_at` 字段稳定。前端只将事件渲染为进度、审批、澄清和报告状态，不持有工作流真相。

## 外部数据同步

前端入口只从任务主页右上角进入 `/tasks/new`；左侧栏移除重复入口不影响以下 API 和权限边界。

CSV 继续使用现有上传流程。API 和数据库连接器在真实连接器未完成前，后端应返回能力元数据或明确错误，例如：

```json
{
  "kind": "database",
  "supported": false,
  "reason": "数据库连接器尚未启用"
}
```

前端会显示“不支持真实 API/数据库连接”，不会上传伪造文件、模拟成功或创建假任务。连接器正式可用后，建议在任务启动响应中返回 `connector_capabilities`，包括 `read`, `write`, `version_check`, `rollback` 四项能力；缺少任一能力时仍须阻止对应操作。

## 错误、空态和权限

- `GET /api/agent/history` 超时或 5xx：前端保留页面结构，指标显示“暂时无法加载”，不降级为演示数字。
- 空历史：显示“暂无任务”和空状态说明。
- `deletion_eligible=false`、`rollback_eligible=false` 必须由后端决定，前端只隐藏或禁用对应按钮。
- 所有聚合结果必须遵守当前租户和操作者权限，前端不传入或信任操作者身份。

## 验收标准

1. 任一指标都能追溯到 API 响应字段或明确显示空态。
2. 不支持的 API/数据库连接器不会显示“连接成功”或创建可执行任务。
3. 游标分页不会被前端误称为完整时间范围统计。
4. 事件字段稳定，前端可持续展示任务阶段与终态报告。
