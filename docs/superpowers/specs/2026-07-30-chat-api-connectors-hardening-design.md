# 聊天 API 连接器合同加固与代码简化设计

## 文档状态

- 日期：2026-07-30
- 范围：`codex/add-chat-api-connectors` 工作树
- 基线：
  `docs/superpowers/specs/2026-07-29-chat-driven-api-connectors-design.md`
- 目标：修复代码审查发现的冻结重放、安全配置、供应商请求和聊天配置缺口，并在行为受测试保护后精简本次变更引入的无用或重复代码。

## 结论

采用版本化合同方案：

1. 持久化不可变的来源角色绑定，不再从可变任务意图或当前服务配置重新推导数据库运行合同。
2. Provider Registry 同时保存当前版本和仍被历史任务引用的旧版本 Adapter。
3. 一次性配置会话持久化到数据库，并以租户、Provider、过期时间和消费状态约束。
4. DingTalk 访问令牌不进入 URL；可重试供应商错误使用有界指数退避。
5. 安全配置卡收集受审计的非敏感组织配置，不再固定把所有人员解释为教师。
6. 功能测试全部转绿后，仅在本变更范围内删除死代码、重复转换和无价值包装。

## 非目标

- 不引入任意 OpenAPI、任意 URL、模型 HTTP 工具或供应商写入能力。
- 不重新设计 Agent Graph 拓扑、风险审批、SQL 执行或回滚合同。
- 不把数据库 DSN、API 密钥或 access token 写入任务、绑定、事件或模型上下文。
- 不对本分支未修改的旧模块做顺手重构。
- 不以减少行数为目标牺牲清晰度、错误处理或审计信息。

## 版本化来源角色绑定

新增持久化 `AgentSourceBindingRecord`，每个任务和角色最多一条，至少保存：

- `task_id`
- `tenant_id`
- `role`
- `connector_kind`
- `configuration_id`
- `snapshot_id`
- `configuration_fingerprint`
- `frozen_public_configuration`
- `credential_reference`
- `mapping_checkpoint_key`
- `normalization_checkpoint_key`

API 权威绑定引用 `ApiAuthoritySourceRecord` 和其物化后 Snapshot。数据库目标绑定保存
`DatabaseConnectorConfiguration` 的安全序列化，不保存 DSN；`credential_reference` 必须仍是服务端密钥引用。

任务创建在同一事务中写入绑定。运行时按任务和角色读取绑定，并校验：

- tenant、task、role 和 connector kind 一致；
- Snapshot 与任务及角色一致；
- 安全配置序列化后的哈希等于冻结指纹；
- 当前 Secret Store 仍能解析冻结的 credential reference；
- 不读取当前 `database_connector_configurations[configuration_id]` 来改变表、字段映射或角色。

数据库运行时新增从冻结 `DatabaseConnectorConfiguration` 和服务端 credential reference
构造连接器的入口。密钥内容允许在同一不可变 reference 后端完成凭据轮换，但 reference
不能被历史任务替换为另一个引用。

旧 v1/v2 运行保持现有路径；只有 `source-ingestion-v3` 使用持久化角色绑定。

## Provider 和 Adapter 版本恢复

`ProviderRegistry` 使用以下键保存 Adapter：

```text
provider_id
manifest_version
adapter_version
```

每个 Provider 另有一个当前版本，用于新连接和连接测试。任务物化使用
`ApiAuthoritySourceRecord` 冻结的三个版本精确解析 Adapter，而不是读取当前版本后比较失败。

注册规则：

- 同一个完整版本键只能注册一次；
- 每个 Provider 只能有一个显式当前版本；
- 当前版本必须已注册；
- 历史版本可以继续注册，但不会出现在新连接默认选择中；
- 未部署冻结版本时返回稳定的 `connector_provider_contract_unavailable`，不静默升级。

当前 DingTalk 和 WeCom 仍各只有一个实现；本次结构保证以后发布新版本时可以同时保留旧实现。

## 一次性安全配置会话

新增 `ApiConfigurationSessionRecord`，保存：

- 随机 UUID
- `tenant_id`
- `provider_id`
- `expires_at`
- `consumed_at`
- 创建时间

创建连接时使用 `SELECT ... FOR UPDATE` 校验并消费会话。只有未过期、未消费、租户和 Provider
都匹配的会话有效。连接创建失败时整个事务回滚，会话不会被错误消费；连接创建成功后会话与
连接在同一事务提交。

过期会话不需要同步删除才能保证安全；删除由后续维护任务处理。普通响应不包含内部消费状态。

## 供应商 HTTP 安全与恢复

### DingTalk token

DingTalk Adapter 使用官方支持的 header token 调用方式。access token 不进入 URL、异常、
checkpoint 或日志。实现前核对当前官方接口合同，并把固定 host、固定 path 和 header 名称写入
Adapter 测试。

### 有界重试

公共请求函数仅重试以下临时错误：

- connect/read timeout
- transport unavailable
- HTTP 429
- HTTP 5xx

认证、权限、400、其他 4xx、无效 JSON 和业务错误码不重试。

默认最多三次请求。等待时间优先使用有界 `Retry-After`，否则按固定指数序列递增；等待函数可
注入测试替身，测试不执行真实 sleep。重试结束后仍只抛出稳定安全错误码。

## 安全配置卡

Provider Manifest 对外声明受审计的 public configuration 字段。第一版配置卡支持：

- 显示名称
- 默认人员实体类型：教师或学生
- 根部门 ID
- 可选业务编号字段
- 学生场景下的可选班级字段

不允许任意 JSON 编辑器。部门级人员类型规则暂不在聊天卡中开放；若一个连接需要同时区分教师
和学生，后端继续要求通过受审计管理配置提供明确规则，不能让模型或默认逻辑猜测。

前端提交实际用户选择，连接测试返回对应实体 capability 和 visibility。连接错误用中文安全
说明展示，并保留重新配置和测试动作，不直接显示内部错误正文。

## 连接新鲜度

任务创建要求连接 `state=active` 且 `last_tested_at` 未超过服务端配置的最大有效期。默认有效期
为 24 小时。过期连接返回稳定的连接需重新测试错误，不创建任务或学校锁。

## 数据迁移

扩展当前未合并的 `0039_api_connectors` 迁移，增加：

- `api_configuration_sessions`
- `agent_source_bindings`

迁移保持全新增量，不修改旧业务表数据。模型元数据测试和全新 PostgreSQL 迁移烟测必须覆盖
新增表、唯一约束、外键和检查约束。

## 测试策略

所有行为修改采用 TDD：

1. 先增加失败测试并确认失败原因对应缺口。
2. 写最小实现使聚焦测试通过。
3. 每个行为组转绿后再重构。

必须覆盖：

- 数据库安全配置在任务创建后改变，v3 仍使用冻结绑定；
- 绑定的 snapshot、tenant 或配置指纹变化时 fail closed；
- 同一 Provider 的新旧 Adapter 可同时注册，旧任务解析旧版本；
- 冻结版本缺失时不自动升级；
- 配置会话跨应用实例可消费、只能消费一次、过期和跨租户均拒绝；
- DingTalk token 只在 header，不出现在 URL；
- 429、5xx、timeout 按有界次数退避，权限错误不重试；
- 教师和学生配置分别产生正确 capability、visibility 和任务资格；
- 连接测试过期时任务创建被拒绝；
- v1/v2、远程 CSV、数据库/数据库和现有治理链保持不变。

## 代码简化

功能修复全部转绿后执行一次单独的简化阶段，范围仅限 `master...HEAD` 中新增或修改的连接器、
v3 接入、身份绑定和对话配置代码。

审查方法：

- 使用 Ruff、Mypy、TypeScript 编译和未使用符号检查发现死代码；
- 使用符号引用搜索确认仅定义未调用的内部 helper；
- 比较 DingTalk/WeCom、API/数据库角色路由、schema/view 转换中的重复逻辑；
- 检查超长函数、重复校验、重复 hash/排序/安全错误映射；
- 删除已被持久化绑定替代的 task-intent 派生路径；
- 只提取具有共同稳定语义的 helper，不制造通用万能 Provider 抽象。

删除或合并每一处代码后立即运行对应聚焦测试。测试若需修改才能保持通过，视为行为变化，不纳入
纯简化提交。

## 验收标准

完成必须同时满足：

1. 六项代码审查问题均有先失败后通过的自动化回归测试。
2. source-ingestion-v3 的数据库目标和 Provider Adapter 都能按冻结合同恢复。
3. 历史任务不会因当前配置或当前 Adapter 变化而静默改用新合同。
4. access token 不进入 DingTalk URL。
5. 配置会话支持多进程/重启后的数据库共享状态并只能消费一次。
6. 聊天配置卡能够配置教师或学生连接，不再硬编码教师。
7. 临时供应商错误执行有界指数退避，永久错误立即失败。
8. 连接测试新鲜度在任务创建前验证。
9. 本次 11k+ 行改动中的确认死代码和重复实现被删除或合并，行为保持不变。
10. 后端 pytest、Ruff、Mypy、前端测试、lint、typecheck、build、Playwright、严格
    OpenSpec 校验和 PostgreSQL 全新迁移烟测全部通过。
