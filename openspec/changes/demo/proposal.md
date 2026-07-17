## Why

希沃与第三方教学平台分别维护完整的学校组织架构，但教师、学生、班级和部门数据会出现缺失、冗余、属性冲突和层级不一致。需要一个可演示、可追溯的前后端系统，从 CSV 数据源开始完成对账、AI 分析、人工审核、治理执行、报告和回滚，并为未来接入真实 API 保留稳定扩展点。

## What Changes

- 建立 Python 3.12、FastAPI、Pydantic 和 PostgreSQL 后端基础，并提供异步任务扩展边界。
- 支持上传第三方与希沃 CSV，校验格式、映射字段并生成不可变数据快照。
- 将不同来源数据转换为统一组织实体模型，执行分层候选召回、实体匹配和一对一冲突处理。
- 检测希沃缺失、希沃冗余、属性冲突、结构冲突和重复冲突。
- 对每条差异强制生成成因、证据、风险、置信度和治理建议；使用 Agent、Skill 与受控 MCP 工具处理语义推理。
- 提供人工勾选、批量确认、执行前检查、CSV 目标修正、回查验证和不可变执行记录。
- 支持从执行记录按需生成治理报告，并通过补偿操作创建可审计的回滚批次。
- 建立简洁的 Web 工作台，覆盖任务创建、进度、差异审核、执行监控、历史记录、报告和回滚。
- 定义通用 Connector 接口；本变更实现 CSV Connector，希沃和第三方 API Connector 仅保留接口与测试替身。

## Capabilities

### New Capabilities

- `data-source-ingestion`: CSV 上传、格式校验、字段映射、标准实体转换、快照和 Connector 扩展契约。
- `organization-entity-resolution`: 组织实体标准化、历史映射、精确匹配、候选召回、评分和匹配冲突处理。
- `reconciliation-differences`: 对已匹配和未匹配实体生成结构化差异及证据。
- `ai-governance-analysis`: 使用 Agent、Skill 和 MCP 生成强制成因分析与结构化治理建议。
- `governance-execution`: 审核、批量执行、CSV 目标版本、验证、审计和部分失败处理。
- `governance-reporting-rollback`: 按需报告、回滚预检查、补偿计划和不可变回滚记录。
- `reconciliation-web-workbench`: 完整前端任务、差异、执行记录、报告和回滚工作流。

### Modified Capabilities

无。

## Impact

- 新增独立的 `backend/`、`frontend/` 和测试目录，以及本地开发配置。
- 新增 PostgreSQL、SQLAlchemy、Alembic、CSV 处理、模型调用、Embedding/pgvector 和 MCP 相关依赖；Redis/Celery 在异步阶段引入。
- 新增前后端 REST API，并使用 SSE 提供长任务进度。
- 当前数据读写以版本化 CSV 和数据库快照为准，不覆盖上传原文件，也不调用真实希沃或第三方写接口。
- 当前允许将数据发送到外部模型 API；敏感数据脱敏和私有模型部署保留为后续安全增强。
