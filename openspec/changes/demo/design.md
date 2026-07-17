## Context

当前仓库只有任务文档和 OpenSpec 配置，没有应用代码、数据库或测试框架。系统目标是以第三方组织数据为权威源，发现其与希沃组织数据的差异，并通过人工审核将治理结果落到希沃。首个可运行版本使用两份 CSV 模拟双方数据和目标写入，同时为真实 API Connector 保留接口。

完整链路跨越文件接入、标准化、实体匹配、差异检测、AI 分析、前端审批、执行验证、审计、报告和回滚，因此需要清晰的领域边界。当前允许外部 LLM 与 Embedding API 处理数据；脱敏和私有部署不属于本变更。

## Goals / Non-Goals

**Goals:**

- 建立可逐块实施、可独立测试的前后端工程目录。
- 完成“上传双方 CSV 到输出修正后希沃 CSV”的完整演示闭环。
- 对每条差异提供执行前强制成因分析和治理建议。
- 保证原始文件、快照、分析、执行和回滚记录可追溯。
- 通过 Connector、模型 Provider 和 MCP 工具边界支持后续替换外部系统。
- 让前端完整覆盖任务、差异、执行、报告与回滚工作流。

**Non-Goals:**

- 本变更不接入真实希沃、钉钉或其他第三方生产 API。
- 本变更不实现敏感数据脱敏、私有模型部署或企业单点登录。
- 本变更不自动执行高风险删除，不允许 Agent 绕过审批写入目标。
- 本变更不以微服务拆分为目标；先实现边界清晰的模块化单体。

## Decisions

### 1. Use a modular monorepo

仓库采用前后端分离的单仓结构：

```text
.
├── backend/
│   ├── pyproject.toml
│   ├── alembic.ini
│   ├── alembic/
│   │   └── versions/
│   ├── app/
│   │   ├── main.py
│   │   ├── core/
│   │   │   ├── config.py
│   │   │   ├── database.py
│   │   │   ├── errors.py
│   │   │   ├── logging.py
│   │   │   └── security.py
│   │   ├── api/
│   │   │   ├── dependencies.py
│   │   │   └── routes/
│   │   │       ├── health.py
│   │   │       ├── uploads.py
│   │   │       ├── reconciliation_tasks.py
│   │   │       ├── differences.py
│   │   │       ├── execution_batches.py
│   │   │       ├── execution_records.py
│   │   │       ├── reports.py
│   │   │       └── rollbacks.py
│   │   ├── schemas/
│   │   │   ├── common.py
│   │   │   ├── canonical_entities.py
│   │   │   ├── ingestion.py
│   │   │   ├── matching.py
│   │   │   ├── differences.py
│   │   │   ├── governance.py
│   │   │   ├── executions.py
│   │   │   └── reports.py
│   │   ├── models/
│   │   │   ├── base.py
│   │   │   ├── reconciliation.py
│   │   │   ├── snapshots.py
│   │   │   ├── mappings.py
│   │   │   ├── differences.py
│   │   │   ├── executions.py
│   │   │   └── reports.py
│   │   ├── repositories/
│   │   │   ├── tasks.py
│   │   │   ├── snapshots.py
│   │   │   ├── mappings.py
│   │   │   ├── differences.py
│   │   │   ├── executions.py
│   │   │   └── reports.py
│   │   ├── connectors/
│   │   │   ├── base.py
│   │   │   ├── registry.py
│   │   │   ├── csv_source.py
│   │   │   ├── csv_target.py
│   │   │   ├── seewo_api.py
│   │   │   └── third_party_api.py
│   │   ├── ingestion/
│   │   │   ├── csv_reader.py
│   │   │   ├── encoding.py
│   │   │   ├── field_mapping.py
│   │   │   ├── schema_validation.py
│   │   │   └── quarantine.py
│   │   ├── normalization/
│   │   │   ├── text.py
│   │   │   ├── identifiers.py
│   │   │   ├── organization.py
│   │   │   └── pipeline.py
│   │   ├── snapshots/
│   │   │   ├── service.py
│   │   │   └── hashing.py
│   │   ├── matching/
│   │   │   ├── service.py
│   │   │   ├── exact_matcher.py
│   │   │   ├── candidate_retriever.py
│   │   │   ├── vector_index.py
│   │   │   ├── scorer.py
│   │   │   └── conflict_resolver.py
│   │   ├── differences/
│   │   │   ├── detector.py
│   │   │   ├── field_policies.py
│   │   │   └── classifier.py
│   │   ├── ai/
│   │   │   ├── agent.py
│   │   │   ├── providers/
│   │   │   │   ├── base.py
│   │   │   │   ├── llm.py
│   │   │   │   └── embeddings.py
│   │   │   ├── mcp/
│   │   │   │   ├── server.py
│   │   │   │   └── tools/
│   │   │   │       ├── difference_context.py
│   │   │   │       ├── candidate_search.py
│   │   │   │       ├── mapping_rules.py
│   │   │   │       └── execution_context.py
│   │   │   └── skills/
│   │   │       ├── analyze-data-difference/
│   │   │       ├── resolve-ambiguous-entity/
│   │   │       ├── generate-governance-plan/
│   │   │       ├── assess-rollback-impact/
│   │   │       └── generate-governance-report/
│   │   ├── governance/
│   │   │   ├── plan_builder.py
│   │   │   ├── plan_validator.py
│   │   │   ├── risk_policy.py
│   │   │   └── dependency_graph.py
│   │   ├── executions/
│   │   │   ├── executor.py
│   │   │   ├── preflight.py
│   │   │   ├── verifier.py
│   │   │   ├── compensation.py
│   │   │   └── csv_versioning.py
│   │   ├── reports/
│   │   │   ├── generator.py
│   │   │   └── renderer.py
│   │   └── workers/
│   │       ├── celery_app.py
│   │       ├── reconciliation.py
│   │       ├── analysis.py
│   │       ├── execution.py
│   │       └── reporting.py
│   ├── tests/
│   │   ├── unit/
│   │   ├── integration/
│   │   ├── contract/
│   │   └── fixtures/
│   └── storage/
│       ├── uploads/.gitkeep
│       ├── snapshots/.gitkeep
│       ├── exports/.gitkeep
│       └── reports/.gitkeep
├── frontend/
│   ├── package.json
│   ├── vite.config.ts
│   ├── src/
│   │   ├── main.tsx
│   │   ├── app/
│   │   │   ├── router.tsx
│   │   │   ├── providers.tsx
│   │   │   └── layout/
│   │   ├── api/
│   │   │   ├── client.ts
│   │   │   ├── uploads.ts
│   │   │   ├── tasks.ts
│   │   │   ├── differences.ts
│   │   │   ├── executions.ts
│   │   │   ├── reports.ts
│   │   │   └── rollbacks.ts
│   │   ├── features/
│   │   │   ├── dashboard/
│   │   │   ├── task-create/
│   │   │   ├── task-detail/
│   │   │   ├── difference-workbench/
│   │   │   ├── batch-confirmation/
│   │   │   ├── execution-monitor/
│   │   │   ├── execution-history/
│   │   │   ├── execution-detail/
│   │   │   ├── report-viewer/
│   │   │   └── rollback-review/
│   │   ├── components/
│   │   │   ├── status/
│   │   │   ├── tables/
│   │   │   ├── entity-diff/
│   │   │   └── feedback/
│   │   ├── hooks/
│   │   ├── types/
│   │   ├── utils/
│   │   └── styles/
│   ├── tests/
│   │   ├── unit/
│   │   └── e2e/
│   └── public/
├── infra/
│   ├── docker-compose.yml
│   └── env.example
├── docs/
│   ├── api/
│   └── sample-data/
└── openspec/
```

选择模块化单体而非微服务，因为当前团队需要按功能逐步完成，跨服务事务和部署成本没有收益。模块边界保留未来拆分可能。

### 2. Adopt a CSV-first connector contract

`SourceConnector` 负责读取实体与版本信息，`TargetConnector` 额外负责应用操作和验证。CSV Connector 为首个实现；API 文件只定义明确的未实现边界和契约测试替身。

CSV 执行永不覆盖上传文件。每个执行批次从目标快照派生新版本，输出到受控存储并保存父版本、哈希和执行批次 ID。该方案能在没有真实希沃 API 时演示完整修正和回滚。

替代方案是直接在业务服务中读取 CSV；这会让未来 API 接入侵入所有下游模块，因此拒绝。

### 3. Separate canonical schemas from persistence models

Pydantic v2 定义 API 请求、响应、标准实体和 AI 结构化输出；SQLAlchemy 2 定义持久化模型。CSV 使用 Polars 做批量读取和列级处理，再在领域边界转换为 Pydantic 模型。

标准实体覆盖 `OrganizationUnit`、`Class`、`Teacher`、`Student` 和 `Membership`，同时保留来源系统、来源 ID、原始行号、快照 ID 和原始 payload 引用。

### 4. Use layered entity resolution

实体匹配按组织依赖顺序执行：部门/年级、班级、教师、学生、归属关系。每类实体依次使用历史映射、稳定标识、Blocking、词法与 pgvector Top-K、多字段评分、一对一冲突处理，最后才调用 LLM 判断少量模糊候选。

Embedding 只负责候选召回，不单独作为最终匹配结论。人工确认的映射持久化并优先复用。

替代方案是全量 LLM 两两判断；其成本和平方级比较量不可接受，因此拒绝。

### 5. Keep governance workflow deterministic

任务状态保存在 PostgreSQL。第一阶段通过同步应用服务完成最小闭环，服务接口保持可由 Celery 调用；性能阶段再引入 Redis、Celery 和 SSE。Celery 不是状态事实来源。

差异检测、风险门禁、依赖排序、执行、幂等、验证、审计和补偿由确定性服务负责。Agent 只能生成分析、建议、解释和报告。

### 6. Constrain Agent and MCP responsibilities

一个组织数据治理 Agent 使用版本化 Skill 完成成因分析、模糊匹配、方案建议、回滚影响解释和报告生成。MCP Server 只暴露读取差异、搜索候选、读取映射规则和读取执行上下文等受控工具。

Agent 输出通过 Pydantic 和业务规则校验。CSV 写入、未来 API 写入和回滚执行不作为 Agent 可自由调用的 MCP 工具；它们必须经过后端审批凭证和 Execution Service。

### 7. Use append-only execution and compensation

执行批次保存计划版本、操作人、预检查结果和逐项状态。每个操作保存 before/after、目标版本、错误与验证结果。批次可部分成功，重试只处理符合条件的失败项。

外部目标不能参与数据库事务，因此回滚采用 Saga 式补偿：先做依赖与漂移预检查，再生成反向操作，用户确认后创建新批次。原执行记录和报告不可修改。

### 8. Build a quiet operational frontend

前端采用 React、TypeScript、Vite、React Router、TanStack Query 和 Ant Design。界面以任务和表格工作流为中心，不使用营销式页面。长任务使用 SSE；连接失败时退回轮询。

前端仅提交选择和命令，不计算权威风险、不生成操作人 ID，也不自行判断是否可回滚。所有动作门禁以后端状态为准。

### 9. Persist the minimum domain data

核心数据表包括：

- `reconciliation_tasks`：任务、阶段、状态和进度。
- `source_files` 与 `snapshots`：文件元数据、哈希、版本和存储位置。
- `canonical_entities`：标准实体与原始引用。
- `entity_mappings`：跨系统映射、证据和确认来源。
- `difference_items` 与 `analysis_results`：差异和强制分析。
- `governance_plans` 与 `execution_batches`：批准计划和批次。
- `execution_operations`：逐项 before/after、结果和验证。
- `governance_reports`：版本化报告。
- `rollback_links`：原批次与补偿批次关系。

## Risks / Trade-offs

- [CSV 表头和数据规则尚未确认] → 使用可配置字段映射、隔离区和脱敏样例驱动的契约测试。
- [外部模型输出不稳定] → 严格 Pydantic 校验、结构化输出、重试上限、人工审核和完整模型溯源。
- [大 CSV 导入占用内存] → 使用 Polars 流式/批量读取、数据库批量写入和阶段性指标。
- [Celery 引入过早增加复杂度] → 先实现同步可测试服务，达到闭环后再包装为后台任务。
- [CSV 回滚不等于真实 API 回滚] → 用版本化目标和补偿操作保持语义一致，并在 API Connector 接入时重新验证可逆性。
- [Agent/MCP 被误用为业务编排器] → 明确只读工具和审批边界，以后端状态机作为唯一控制器。
- [允许外部模型处理敏感数据产生合规风险] → 将模型 Provider 集中封装并记录数据发送范围，后续可增加脱敏中间层。

## Migration Plan

1. 创建前后端骨架、数据库迁移和本地 PostgreSQL 开发环境。
2. 上线 CSV 接入、标准模型、快照和基础精确匹配闭环。
3. 增加差异工作台和版本化 CSV 执行，验证业务闭环。
4. 增加 Embedding、Agent、Skill、MCP 与强制分析门禁。
5. 增加执行历史、报告和回滚补偿。
6. 在数据量和用户体验需要时引入 Redis、Celery 和 SSE。
7. 获取真实 API 文档后实现 Connector，并通过同一契约测试替换 CSV 目标。

每阶段可通过关闭对应路由或功能开关回退。数据迁移通过 Alembic 管理；上传原文件与历史执行记录不随功能回退删除。

## Open Questions

- 双方 CSV 的实际表头、编码、实体拆分方式和脱敏样例是什么？
- 教师、学生、班级和部门各自有哪些跨系统稳定标识？
- 第一版需要同时支持全部实体，还是先以部门和教师作为垂直切片？
- 外部 LLM 与 Embedding Provider 是否有指定模型、限额和网络要求？
- 演示身份采用固定后端用户，还是实现基础管理员/操作员权限？
- 治理报告第一版需要 HTML、PDF，还是同时支持两者？
