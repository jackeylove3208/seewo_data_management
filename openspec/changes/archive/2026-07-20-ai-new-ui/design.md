## Context

当前仓库的 FastAPI 后端已经实现 CSV 上传和快照、实体解析、差异检测、单建议 AI 分析以及只读 MCP 工具。React 前端可以创建任务，但任务详情和差异工作台主要读取浏览器本地记录与演示数据。前端创建任务时固定提交 `tenant_id=demo-school`，后端操作人上下文使用 `school-1`，因此真实任务虽然能完成数据接入，却会在受租户保护的差异或分析接口上返回 404。

现有 `HttpLLMProvider` 已按 OpenAI Chat Completions 风格发送结构化请求，但运行环境未配置 URL 和 API Key，当前测试只使用模型替身。现有 `CauseAnalysis` 只有一个 `recommended_action`，不能表达多种可选方案，也没有人工修改形成后端治理方案的接口。

本变更跨越工作流编排、模型安全边界、结构化领域模型、数据库迁移和前端交互。第一版继续使用同步 FastAPI 服务和前端轮询，不提前引入 Celery；治理方案只保存为待执行记录，不写回目标 CSV。

## Goals / Non-Goals

**Goals:**

- 用户创建任务后，Web 界面自动推进数据接入、实体解析、差异检测和强制 AI 分析。
- 真实语义分析通过 OpenAI 兼容企业网关完成，配置和密钥只存在后端。
- 初始提示词和所有 MCP 工具结果在出站前经过同一任务级令牌化边界。
- 每条差异产生可解释、可校验的零至三个 AI 治理方案；零方案表示只能人工处理。
- 用户可在差异弹窗中选择 AI 方案，或通过字段白名单人工生成方案。
- 两种方案都持久化为可由下一模块消费的待治理执行方案。
- 前端使用真实 API 并覆盖加载、分析动画、空状态、失败、重试和版本冲突。

**Non-Goals:**

- 不在本变更中执行治理方案、生成新希沃 CSV、调用希沃写 API、生成报告或实现回滚。
- 不引入 Celery、Redis 或跨进程实时推送；这些仍属于后续异步性能模块。
- 不把 API Key、企业网关配置或令牌映射发送到浏览器。
- 不允许 Agent 或 MCP 直接修改目标数据，也不允许模型输出绕过后端操作和字段策略。
- 不实现任意原始 CSV 字段编辑；只开放规范实体字段白名单。

## Decisions

### 1. Use a backend-owned resumable workflow with bounded advancement

新增 `ReconciliationWorkflowService` 和 `POST /api/reconciliation-tasks/{task_id}/workflow/advance`。每次调用只完成下一个阶段，或处理一个有上限的 AI 分析批次，然后返回最新状态。任务详情页在可推进状态下自动调用该端点，并通过 TanStack Query 轮询任务状态；页面刷新后根据数据库状态继续，而不是从头开始。

数据库以 `reconciliation_tasks.stage/status` 保存当前摘要，并新增阶段运行记录，保存阶段、尝试次数、开始/结束时间、处理数量和结构化错误。服务对任务行加锁，复用各阶段已有的幂等语义，避免两个浏览器重复生成匹配、差异或分析。失败响应包含 `retryable` 和稳定错误码；用户只对可重试阶段显示“重试”。

考虑过三种方式：前端依次直接调用 `resolve`、`detect`、`analyses` 最简单，但顺序、刷新恢复和并发控制会散落在浏览器；FastAPI `BackgroundTasks` 无法在进程重启后可靠恢复；立即引入 Celery 会超出当前模块。受控的 `advance` 端点在现有同步架构下最可测试，后续可以由 Celery 调用同一服务。

### 2. Make tenant identity backend-owned

`CreateReconciliationTaskRequest` 删除 `tenant_id`，任务路由注入 `OperatorContext`，由后端把 `operator.tenant_id` 写入快照范围。任务读取、推进、差异、分析和方案接口统一校验后端操作人租户；未授权资源一律返回 404，避免泄漏资源是否存在。

这是一项当前内部 API 的有意变更。前端删除固定租户，测试改为通过后端依赖覆盖设置操作人。未来接入认证系统时只替换 `OperatorContext` 来源，不改业务请求体。

### 3. Extend the provider as a configurable OpenAI-compatible enterprise gateway

生产运行只实例化真实 HTTP provider；单元测试使用 `httpx.MockTransport` 或本地假网关验证协议，不在 CI 调用收费模型。网关 URL 是完整的 Chat Completions 地址，例如企业网关提供的 `/v1/chat/completions`。

真实值填写在 Git 已忽略的 `backend/.env`，字段由 `backend/app/core/config.py` 校验，示例写在 `backend/.env.example`：

```dotenv
RECONCILIATION_LLM_URL=https://gateway.example.com/v1/chat/completions
RECONCILIATION_LLM_API_KEY=replace-with-real-secret
RECONCILIATION_LLM_MODEL=enterprise-model-name
RECONCILIATION_LLM_AUTH_HEADER=Authorization
RECONCILIATION_LLM_AUTH_SCHEME=Bearer
RECONCILIATION_LLM_RESPONSE_MODE=json_schema
RECONCILIATION_LLM_EXTRA_HEADERS_JSON={}
RECONCILIATION_LLM_EXTRA_BODY_JSON={"top_p":0.8}
RECONCILIATION_LLM_TIMEOUT_SECONDS=20
RECONCILIATION_MODEL_RETRY_ATTEMPTS=3
RECONCILIATION_MODEL_RETRY_WAIT_SECONDS=0.2
RECONCILIATION_TOKENIZATION_SECRET=replace-with-a-long-random-secret
```

`RECONCILIATION_LLM_EXTRA_BODY_JSON` 是用户所需 `kwargs` 的安全等价形式。Pydantic 将其解析为 JSON 对象；`model`、`messages`、`response_format` 和 `stream` 等保留键不能被覆盖。额外请求头同样校验为字符串映射，不能覆盖认证头或 `Content-Type`。`response_mode` 支持 `json_schema`、`json_object` 和 `prompt_json`，以适配不同企业网关；无论网关是否原生支持 JSON Schema，响应最终都必须经过 Pydantic 和业务策略校验。

启动与 `/health/ready` 只报告“已配置/未配置”，不得回显密钥或额外请求头。模型错误日志记录网关请求 ID、状态码和稳定错误码，不记录请求正文。

### 4. Put tokenization around every model-visible payload

新增 `TaskTokenizationContext`，以服务器密钥、租户 ID、任务 ID、字段类别和规范化值计算 HMAC 令牌。教师/学生姓名、手机号、邮箱、第三方外部 ID 和希沃外部 ID 分别转换为可辨别类型但不可反推的稳定值，例如 `PERSON_NAME_A81F2C`。组织和班级的非个人名称默认保留，以维持层级分析语义；字段策略允许以后扩大令牌化范围。

Agent 在构建首轮消息前调用 tokenization gateway，MCP gateway 在把每次工具结果加入消息前调用同一上下文。当前分析调用内保存令牌到原值的内存映射，模型返回后立即反向映射；稳定令牌由 HMAC 生成，因此不同调用看到同一任务值时结果一致。数据库、提示词日志、工具轨迹和异常不得保存反向映射。

模型只能引用本次上下文中出现过的令牌。未知令牌、新造手机号/邮箱、与权威快照不一致的修改值均被视为无效输出并转人工处理。相比仅遮罩字符串，此方案保留跨消息相等关系；相比将令牌映射持久化，减少了新的敏感数据存储面。

### 5. Introduce versioned multi-option analysis

新增 `analysis-v2` 输出：

```text
CauseAnalysisV2
├── cause
├── evidence_summary
├── manual_only
├── manual_reason
└── options[0..3]
    ├── option_id
    ├── operation_type
    ├── target_entity_id
    ├── proposed_changes[{field, before, after}]
    ├── rationale
    ├── evidence_refs[]
    ├── risk
    ├── confidence
    ├── preconditions[]
    └── recommended
```

`manual_only=true` 时必须没有方案并提供原因；否则必须有一至三个方案，且恰好一个标记为推荐。后端验证操作类型与差异类型相容、目标实体属于当前快照、before 值没有漂移、after 值来自权威证据、字段在策略白名单、证据引用存在。高风险、身份不确定、父级映射不确定、信息不足或模型输出连续无效时只允许人工处理。

希沃缺失和明确冗余等规则可确定的差异继续使用确定性分析，其他语义冲突必须真正调用企业网关。每个结果绑定差异版本，并记录 provider、model、Skill、prompt、工具轨迹、token usage 和时间。历史 `analysis-v1` 保留只读；新请求默认读取 `analysis-v2`，不得静默覆盖旧记录。

### 6. Persist AI and operator choices through one proposal contract

新增不可变 `governance_proposals` 记录，核心字段包括任务、差异、差异版本、分析记录、方案版本、`proposal_source`、操作类型、目标实体、before/after、理由、证据、风险、创建人、创建时间、状态和 `supersedes_id`。状态在本变更中只到 `pending_execution`。

AI 路径使用 `POST /api/differences/{id}/proposals/from-analysis`，请求只包含分析 ID、`option_id` 和期望差异版本，后端从已持久化分析复制经过校验的方案，不能接受浏览器重写 AI 内容。人工路径使用 `POST /api/differences/{id}/proposals/manual`，请求包含期望版本、字段修改和人工原因；后端补齐 before 值并再次执行字段、操作、租户和版本校验。

人工方案不是特权绕过。它与 AI 方案共享读取接口、状态和下一阶段契约，只是 `proposal_source=operator`。再次选择或修改会创建新版本并指向被替代记录，保留责任链。

### 7. Replace demo state with a query-driven operational UI

新增 typed API clients 和查询 hooks，真实任务不再使用 `demoDifferences` 或仅依赖 `localStorage`。任务详情展示固定尺寸的四阶段轨道；活动阶段显示旋转 `Sparkles`、脉冲进度点和进度条，并尊重 `prefers-reduced-motion`。分析进度显示总数、完成、仅人工、失败数量，动态内容不改变阶段控件尺寸。

差异页使用后端游标分页和筛选，显示权威值、希沃值、匹配证据及分析状态。点击一条差异打开方案弹窗：分析中显示动画和可理解的进度；成功后显示成因、证据、风险、置信度及一至三个方案；仅人工时显示原因和证据缺口。弹窗不会在批量完成后自动连续弹出。

每个 AI 方案提供“采用并预览”，另有“人工修改”。人工编辑器根据实体类型 schema 生成控件，只开放名称、手机号、邮箱、状态、所属组织或班级等允许字段；内部 UUID、来源 ID、快照和审计字段只读。保存前展示 before/after 和人工原因，成功后将行标记为“待治理执行”。本变更不显示会直接写回数据源的按钮。

### 8. Verify behavior without depending on a paid model

后端测试分为纯单元测试、HTTP provider 契约测试和 API 集成测试。假网关必须验证真实请求结构、不同 response mode、额外参数合并、超时重试和错误解析，并断言请求、日志和工具消息中不存在原始敏感值。另提供显式启用的真实网关 smoke test，只有设置专用环境开关时执行。

前端使用 Vitest/Testing Library 覆盖阶段推进、动画、加载/失败/重试、弹窗方案、manual-only 和人工字段校验；Playwright 使用本地后端和假网关覆盖上传两份 CSV 到生成待执行方案的链路，并在桌面和移动视口检查无重叠。

## Risks / Trade-offs

- [同步阶段推进依赖有页面在轮询] → 页面重新打开时自动恢复；后续 Celery 直接复用同一编排服务以获得无人值守执行。
- [企业网关对 `response_format` 的支持不一致] → 提供三种响应模式，统一执行本地结构校验并把不合规输出转人工。
- [令牌化降低姓名相关的语义判断能力] → 匹配和差异证据先由确定性模块产生，模型只分析治理原因；组织层级非个人名称默认保留。
- [额外请求参数可能削弱结构化输出] → JSON 配置有保留键拒绝列表、类型校验和大小限制，密钥仅允许存在于忽略文件或部署密钥系统。
- [多方案增加模型成本和用户决策负担] → 上限为三个，只突出一个推荐方案；清晰差异继续使用确定性分析。
- [人工字段白名单不能覆盖未知 CSV 扩展列] → 第一版优先保证可治理和可审计，新增字段需先进入规范 schema 与策略配置。
- [分析 v1 与 v2 共存增加读取复杂度] → API 明确返回 `analysis_version`，v1 只读，新方案只能引用 v2。

## Migration Plan

1. 增加阶段运行、analysis-v2 和治理方案表/字段，运行 Alembic 升级；旧任务和 analysis-v1 保持可读。
2. 扩展配置模型和 `.env.example`，在未配置企业网关时将需要模型的差异明确标记为仅人工，不伪装成功。
3. 上线令牌化与企业网关 provider，再启用 analysis-v2 写入。
4. 上线工作流推进接口及后端租户所有权；同一发布中删除前端 `tenant_id`，避免新旧契约混用。
5. 上线真实 API 前端页面和方案接口；保留演示任务入口但隔离于真实任务。
6. 使用合成 CSV 和企业网关测试模型执行 smoke test，核对模型溯源和无敏感值日志后再开放真实数据。

数据库回滚只删除尚未被治理执行消费的新表/字段，不删除已有分析记录。若前端发布失败，可恢复旧页面；后端新接口保持独立，旧读取接口继续可用。租户请求契约的回退必须前后端同时发布。

## Open Questions

没有阻塞产品设计的问题。实施真实 smoke test 前需要由部署人员提供企业网关的完整 Chat Completions URL、模型名、API Key、结构化输出支持方式以及网关要求的附加头或请求参数；这些值不写入仓库。
