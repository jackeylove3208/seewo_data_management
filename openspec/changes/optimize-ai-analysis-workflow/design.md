## Context

当前分析阶段由浏览器反复调用 `workflow/advance` 驱动。后端在一个请求和数据库事务中串行分析最多 10 条差异，模型调用完成前只执行 `flush`，因此其他请求看不到逐项进度；请求取消、进程重启或数据库异常会回滚整个批次。前端只在批次响应后看到 `0 -> 10 -> 20` 的跳变，同一阶段请求失败后还可能停止自动推进。

现有 `analysis-v2` 要求人工模式没有方案，提示词、确定性分析和兜底文案存在英文，前端直接展示 `update`、`phone` 等内部代码。用户又必须逐条采用方案，无法对低风险推荐项做一次完整预览后批量进入治理执行。

本变更必须保留既有租户隔离、任务级令牌化、企业模型网关、不可变审计记录和“方案不等于执行”的安全边界，并与工作区中正在收尾的对账失败恢复修改兼容。

## Goals / Non-Goals

**Goals:**

- 让 AI 分析在页面关闭、请求断开或服务短暂重启后继续运行并恢复可观察进度。
- 逐项持久化结果，避免固定 10 条提交造成的进度跳变和整批回滚。
- 保证每个可读取差异至少有一条中文处置路径，同时不为高风险或证据不足项编造可执行修改。
- 支持任务级和实体类型级批量采用安全推荐项，只创建 `pending_execution` 方案。
- 分析完成前隐藏问题类型对照，完成后只展示真实存在问题的类型。

**Non-Goals:**

- 不在本变更中直接调用希沃写 API、修改 CSV 或实现治理执行/回滚。
- 不引入 Redis、RabbitMQ、Celery 或跨服务消息总线。
- 不自动采用高风险方案，不绕过精确预览、版本校验和后续执行审核。
- 不改写历史 `analysis-v1` 和 `analysis-v2` 记录。

## Decisions

### 1. Use PostgreSQL as the durable analysis queue

新增 `analysis_jobs` 和 `analysis_work_items`。作业保存任务、租户、请求人、分析版本、状态、计数、取消标记和时间；工作项保存差异 ID/版本、状态、尝试次数、下次可用时间、租约、结果 ID、稳定错误码和安全人工兜底。`job_id + difference_id + difference_version` 唯一，创建作业使用幂等键避免重复排队。

独立 worker 用短事务和 `FOR UPDATE SKIP LOCKED` 领取一个工作项并提交租约，然后在无数据库事务状态下调用模型，最后开启新短事务校验租约与差异版本、写入不可变分析结果并完成工作项。租约超时的工作项可被再次领取，指数退避只用于限流、超时和临时网关错误。

相比继续优化同步循环，此方案不依赖页面在线，也不会让外部 HTTP 调用占用任务行锁。相比立即引入 Celery，它复用现有 PostgreSQL，部署和一致性成本更低；未来需要外部队列时可从工作项表通过 outbox 投递。

### 2. Separate workflow advancement from analysis execution

`workflow/advance` 到达分析阶段时只创建或复用活动作业并立即返回，不再执行模型调用。任务详情从活动/最近作业聚合分析进度；作业终态后工作流阶段同步为完成或带失败完成。旧的同步分析接口保留兼容读取，但真实工作台改用作业 API。

worker 入口作为独立 Python 模块运行，开发环境命令为 `.venv/bin/python -m app.ai.worker`。一个进程可配置并发上限，但每个领取单元始终是一条差异。

### 3. Introduce `analysis-v3` with mandatory resolution paths

`AnalysisV3` 包含简体中文的问题标题、成因、证据摘要、业务影响、推荐路径 ID，以及一至三个判别联合解决路径：

```text
auto_executable   -> action(operation, target, field changes)
needs_information -> information requests(question, reason, source hint)
manual_only       -> ordered manual steps
```

恰好一条路径标记推荐。只有低/中风险、证据充分、before/after 和字段策略校验通过的 `auto_executable` 路径可转换为治理方案。高风险、身份或父级不确定、证据不足时只能返回 `needs_information` 或 `manual_only`，但必须包含具体可操作的问题或步骤。

模型结构或中文质量校验失败时，第二次请求携带稳定错误类别进行修复；仍失败或网关不可用时，后端按差异类型生成中文人工兜底路径。内部错误码保留在诊断字段，不进入业务文案。基础设施故障若连兜底都无法持久化，工作项才进入 `failed` 并允许重试。

### 4. Enforce Chinese at prompt, schema policy, fallback, and presentation layers

Skill 和系统提示要求面向学校业务人员的简体中文短句。后端检查用户可见字段的中文字符占比、内部代码模式和允许保留的术语白名单（AI、API、CSV）；字段名、操作、风险、状态和实体类型由本地字典映射，不依赖模型翻译。前端只显示本地化标签，技术 ID 和错误码放入管理员诊断信息。

历史英文分析不原地修改；用户需要新格式时创建 `analysis-v3` 作业重新分析。

### 5. Publish committed progress through SSE with polling fallback

`GET /api/analysis-jobs/{job_id}` 返回持久化计数、当前状态和最近更新时间。`GET /api/analysis-jobs/{job_id}/events` 以 SSE 推送作业快照事件，事件带单调递增游标；断线时前端用最后游标重连，SSE 不可用时每两秒轮询状态接口。进度只按真实终态工作项增加，不做虚假插值。

页面显示“正在分析第 N/M 项”和最近更新时间。活动作业超过租约/心跳阈值但仍有可恢复工作项时显示恢复中；只有作业明确失败或取消才显示重试/继续入口。

### 6. Compute issue summaries in the backend after terminal analysis

新增任务级聚合查询，按实体类型返回问题总数、可生成方案数、需补充信息数、人工处理数和失败数。统计基于全部当前版本差异和 `analysis-v3` 结果，不受差异分页大小影响。

前端在作业未进入终态时完全不渲染“问题类型对照”。终态后只渲染 `issue_count > 0` 的实体类型；全部为零时显示完成空状态。

### 7. Add preview-first batch adoption

批量预览 API 接受任务 ID、可选实体类型和期望作业 ID，服务端重新读取当前差异与推荐路径，返回可采用项以及按原因分组的排除项。默认只纳入推荐的低/中风险 `auto_executable` 路径；高风险、人工路径、信息不足、分析失败、版本漂移和已有当前方案均排除。

确认 API 接受预览令牌和幂等键，服务端再次校验版本和 before 值，然后从不可变分析记录复制内容创建 `pending_execution` 方案。部分失败不回滚已成功方案，响应返回成功、跳过和失败明细；重复幂等请求返回同一结果。该操作不调用任何写数据连接器。

## Risks / Trade-offs

- [数据库同时承担业务存储与任务队列，分析量大时增加负载] -> 为领取查询建立状态/可用时间索引，限制 worker 并发，按租户设置并发和速率上限。
- [worker 崩溃造成工作项暂时停留在运行中] -> 使用租约到期恢复，不依赖进程内状态，并记录最近心跳。
- [SSE 在部分代理环境中不可用] -> 状态接口保持完整，前端自动降级轮询且刷新后恢复。
- [模型仍可能返回英文或难懂内容] -> 提示词、结构校验、中文策略校验和确定性兜底四层共同约束。
- [批量采用时差异可能发生版本漂移] -> 预览和确认都校验差异版本/before 值，漂移项单独跳过并要求刷新。
- [分析 v2/v3 并存增加读取复杂度] -> 新工作流显式请求 v3，历史版本只读，批量接口拒绝旧版本。

## Migration Plan

1. 新增作业、工作项及必要索引，升级 Alembic；旧分析和工作流表保持不变。
2. 上线 `analysis-v3` schema、提示词、策略和仓储读取，先用测试 worker 验证逐项提交和租约恢复。
3. 上线 worker 与作业 API，再把 `workflow/advance` 的分析阶段切换为仅创建作业。
4. 上线聚合摘要、SSE 和前端连续进度；保留轮询降级。
5. 上线批量预览/确认和“一键处理”界面，继续沿用现有逐条与人工方案入口。

回滚应用版本时先停止 worker，旧应用继续读取 v2；新增表可保留以保存审计。只有确认没有 v3 作业或方案被后续治理消费时才允许执行数据库 downgrade。

## Open Questions

没有阻塞实施的问题。生产部署前需要确定 worker 进程数量、每租户并发上限和 SSE 反向代理超时，这些作为配置项提供，不改变接口契约。
