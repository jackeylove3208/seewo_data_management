# 混合数据库 Schema 映射与模型租约可靠性设计

日期：2026-08-02

## 结论

保留 `seewo-data-mysql` 的 `mapping.mode: llm`，继续让 AI 根据受限 Schema 元数据自动识别
字段。修复重点不是把新目标改成显式映射，而是让混合模式正确隔离角色：`explicit` 角色由
后端直接冻结，只有 `llm` 角色进入模型输入。模型结果返回后，后端再合并双方映射并执行完整
合同校验。

同时修复两个放大故障的问题：结构化校验反馈必须提供可执行的安全错误码，避免四次重复生成
同类无效结果；Worker 心跳在尚未发生真实接管时应允许安全续租，避免长模型调用被误判为
`AgentGraphLeaseLost`。

## 问题与根因

### 混合映射组合未被正确建模

旧版第三方 MySQL 使用 `mapping.mode: explicit`，六个业务字段已经由服务器配置确定；默认目标
`seewo-data-mysql` 使用 `mapping.mode: llm`，需要通过数据库 Schema 画像识别字段。

当前双数据库路径只区分“双方映射是否全部完整”，没有按角色区分映射责任。任一角色需要 LLM
时，系统会把双方 Schema 一起提交给模型，并要求模型返回双方映射。因此，模型不仅要识别新
目标，还必须逐字复刻已经由后端确定的旧来源映射。只要来源映射在字段引用、顺序、适用实体或
normalizer 上不完全一致，整个输出就会被拒绝。

现有测试覆盖了“两个角色均为 LLM”和“只有一个数据库角色为 LLM”，但没有覆盖实际生产组合
“explicit 权威来源 + LLM 目标”。

### 模型修复反馈不可操作

Schema 或业务合同校验失败时，普通 `ValueError` 会被压缩为 `$ / ValueError`。后续模型尝试
不知道失败属于角色越界、字段遗漏、引用错误、主键误用、版本列误用还是 normalizer 不匹配，
所以最多四次尝试可能重复同一个错误。

“四次模型尝试”表示一次初始尝试加三次结构修复尝试，并不自动等于模型超时。只有
`model_provider_failure` 或 `model_timeout` 才表示供应商或传输失败；
`model_output_failure` 表示模型已经返回，但输出未通过合同。

### 租约失败掩盖原始模型错误

Graph Worker 默认使用有限租约并定期心跳。当前心跳即使仍持有相同 owner 和 fencing token，
只要执行时间刚超过 `lease_expires_at` 就会放弃租约。长模型调用、数据库连接池等待或事件循环
短时延迟会扩大该窗口，最终用 `AgentGraphLeaseLost` 掩盖原始结构化输出失败。

真正的租约丢失应以数据库行锁下的 owner/token 已改变为准。如果心跳取得行锁时 owner/token
仍相同，说明尚无其他 Worker 完成接管，可以安全续租；如果已经改变，旧 Worker 必须立即停止。

## 目标

- 支持 `explicit → llm`、`llm → explicit` 和 `llm → llm` 三种数据库映射组合。
- 模型只接收配置为 `llm` 的角色画像，不重新解释或覆盖显式映射。
- 合并结果仍满足固定六字段、角色边界、主键/版本列排除和目标写入白名单。
- 相同 Schema 指纹复用已验证映射；Schema 改变后重新分析。
- 每次结构修复得到具体、有限且不泄露数据的反馈。
- 长模型调用持续保有租约；真实接管后旧 Worker 仍被 fencing token 阻断。
- CSV、API 或数据库权威来源写入 `seewo-data-mysql` 时复用同一目标映射语义。

## 非目标

- 不把 `seewo-data-mysql` 改为 `explicit`，也不在 YAML 中硬编码其六字段映射。
- 不允许模型读取原始数据库行、凭据、DSN、任意 SQL 或通用数据库工具。
- 不放宽固定六字段合同，不允许模型增加业务字段。
- 不通过单纯延长租约或取消 fencing 规避并发问题。
- 不修改历史任务已经冻结并成功持久化的映射结果。

## 设计

### 按角色规划映射责任

数据库映射材料加载后，将角色分成两组：

- `explicit_roles`：配置包含完整 `field_columns` 和 `allowed_columns`，由后端确定性编译。
- `llm_roles`：配置为 `mapping.mode: llm`，由模型根据受限 Schema 画像产生候选映射。

若 `llm_roles` 为空，沿用现有确定性路径，不调用模型。若存在 LLM 角色，模型输入中的
`sources` 只包含这些角色。缺席角色的输出数组必须为空，模型不得复刻或修改显式角色。

### 服务端合并与最终校验

模型候选通过 Pydantic 输出 Schema 后，后端执行以下步骤：

1. 为每个 `explicit` 角色从冻结配置生成 `DatabaseFieldMapping`。
2. 从模型输出中提取每个 `llm` 角色的候选映射。
3. 拒绝模型为未请求角色返回非空映射。
4. 合并两组映射，形成完整 `DatabaseSchemaMappingOutput`。
5. 对合并结果执行现有严格校验：六字段覆盖、引用归属、字段唯一性、normalizer、
   `entity_kinds`、主键/版本列排除及显式角色 allow-list 一致性。
6. 只有最终结果通过校验后才写入角色 checkpoint 和 Schema 映射缓存。

显式映射是服务器事实，不能被模型输出覆盖。模型候选也不能扩大目标可写列；目标的实际可写
集合由通过校验并冻结后的映射、主键、版本列和连接器能力共同决定。

### 缓存与任务冻结

缓存键继续包含租户、权威连接器、目标连接器、双方 Schema 指纹、ingestion contract、Skill
名称和版本。缓存值保存最终合并且已验证的完整映射，而不是未合并的模型草案。

- 相同连接器与 Schema 指纹：复用缓存，模型调用数为零。
- 任一 Schema 指纹变化：缓存失效，只向模型提交当前 `llm` 角色画像。
- 新任务：冻结当前连接器配置、Schema 指纹和映射 checkpoint。
- 历史任务恢复：继续使用任务已有的冻结 binding/checkpoint，不读取新的全局配置覆盖它。

### 安全结构化修复反馈

增加领域级映射合同错误类型，公开的修复信息只包含 `path` 和稳定 `code`，例如：

- `role_not_requested`
- `contract_field_duplicated`
- `source_field_duplicated`
- `source_field_unknown`
- `primary_or_version_field_forbidden`
- `normalizer_invalid`
- `entity_kinds_invalid`
- `mapped_and_unresolved_conflict`
- `fixed_field_coverage_incomplete`
- `explicit_mapping_mismatch`

反馈不得包含物理字段值、原始行、凭据、SQL 或无效模型全文。下一次尝试使用前一次的安全错误
码构造 JSON repair request。四次失败后，事件和失败记录保留每次尝试的错误类别及安全修复码，
前端可以区分模型超时与结构化合同失败。

### 租约续期与 fencing

心跳在 `SELECT ... FOR UPDATE` 取得运行记录后按以下规则处理：

- owner 和 lease token 与当前 Worker 一致：即使当前时间刚超过旧 expiry，也允许续期。
- owner 或 token 已改变：返回租约丢失，取消当前处理任务，禁止提交结果。
- 学校锁不存在或 owner run 不一致：单独记录 `school_lock_lost`，不能笼统伪装成模型错误。
- 最终 checkpoint、graph transition 和治理写入继续校验 worker、lease token、attempt count 与
  graph cursor；续租不削弱提交阶段的 fencing。

这不是无限宽限：另一个 Worker 一旦先取得过期运行行锁并写入新 token，旧 Worker 的后续心跳
和提交都会失败。

## 数据流

1. Worker 冻结任务 source bindings，并读取双方数据库 Schema 元数据。
2. 映射规划器将角色划分为 `explicit` 和 `llm`。
3. 后端直接编译 explicit 映射。
4. 模型只接收 LLM 角色的受限 Schema 画像并返回候选映射。
5. 失败时使用安全领域错误码进行最多三次修复。
6. 后端合并 explicit 与 LLM 映射并执行完整校验。
7. 校验通过后写入缓存和各角色 checkpoint，再进入确定性数据规范化。
8. 整个模型等待期间 Worker 独立续租；真实接管后旧结果无法提交。

## 错误处理

- Schema 读取失败：按连接器健康或 Schema 发现错误停止，不调用模型。
- LLM 输出无法满足固定合同：最多四次尝试，保留安全修复码，最终进入
  `blocked_model_error`。
- 模型超时、限流或 5xx：记录 `model_provider_failure`，与输出校验失败分开统计。
- 映射存在 unresolved 字段：保存安全的未解决结果，不读取业务行、不开始写入。
- 缓存完整性失败：拒绝缓存并停止，不静默降级为旧映射。
- 租约真实丢失：取消旧 Worker，禁止 checkpoint、transition 或治理结果提交。

## 测试与验收标准

### 映射组合

- `explicit` 权威 MySQL + `llm` 目标：模型请求只包含目标画像；模型输出的权威映射为空；后端
  合并后双方 checkpoint 均包含正确映射。
- `llm` 权威来源 + `explicit` 目标：模型请求只包含权威画像；目标映射由后端注入。
- `llm` 权威来源 + `llm` 目标：模型接收双方画像并返回双方候选。
- 双方均 `explicit`：模型不得运行。
- CSV/API 权威来源 + `llm` 目标：模型只分析目标数据库角色。

### 校验与缓存

- 模型试图返回未请求角色映射时，收到 `role_not_requested` 修复码。
- 模型使用主键或版本列时，收到 `primary_or_version_field_forbidden`。
- 模型遗漏固定字段且未声明 unresolved 时，收到 `fixed_field_coverage_incomplete`。
- 第二次有效输出通过后，只保存最终有效映射。
- 相同 Schema 指纹的新任务命中缓存且模型调用数为零。
- Schema 指纹变化后重新调用模型，并且不复用旧 checkpoint。

### 租约与并发

- 心跳略晚于 expiry、owner/token 未改变时成功续租。
- 替代 Worker 已写入新 token 后，旧 Worker 心跳失败且处理任务被取消。
- 慢模型调用跨越多个初始租约周期时，心跳持续续期并能提交有效结果。
- 学校锁丢失和运行租约丢失产生不同的安全诊断类别。

### 端到端回归

- 老版本 explicit MySQL 同步学生、教师和部门到 `seewo-data-mysql` 均能完成 Schema 映射，
  不要求模型复刻老来源映射。
- CSV 同步学生、教师和部门到 `seewo-data-mysql` 时，目标映射可以生成并复用。
- 大差异批次不会因为字段映射阶段的无效重复尝试而产生 `AgentGraphLeaseLost`。

## 建议实施顺序

1. 先增加 mixed-mode 映射失败回归测试，确认测试因模型被要求复刻 explicit 角色而失败。
2. 实现按角色划分、服务端合并和最终校验，使 mixed-mode 测试通过。
3. 增加稳定领域修复码及修复请求测试。
4. 增加临界续租与真实接管测试，再修改租约续期判断。
5. 增加 MySQL/CSV 到 `seewo-data-mysql` 的端到端回归。
6. 运行后端完整 pytest、Ruff、mypy、迁移 smoke test 及严格 OpenSpec 校验。

## 影响范围

- `backend/app/agent_graph/production_executor.py`
- `backend/app/ai/graph_subagents.py`
- `backend/app/ai/agent_prompting.py`
- `backend/app/agent_runtime/repository.py`
- `backend/app/agent_graph/worker.py`
- 数据库映射、Graph Worker 和生产运行时相关测试

不需要修改数据库表结构或前端 API 合同。
