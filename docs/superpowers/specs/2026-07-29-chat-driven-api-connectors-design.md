# 聊天驱动的第三方 API 连接器与同步设计

## 背景

当前系统已经具备完整的学校组织数据对账链路：

```text
对话确认同步意图
  -> 创建持久化任务
  -> 获取学校范围锁
  -> 固化并检查来源
  -> 规范化组织数据
  -> 构建身份对应
  -> 分析差异
  -> 风险审批
  -> 编译并执行希沃目标操作
  -> 验证、审计、回滚和报告
```

现有来源主要是本地 CSV、对话中的远程 CSV 和已配置的数据库连接器。Schema 层已经出现
`api` 连接器类型，但当前任务创建会拒绝 API 与数据库的组合，Agent Graph 的来源模式也只
区分 CSV 与数据库，尚无真实钉钉或企业微信运行时。

本功能希望实现以下用户体验：

1. 用户在聊天中说明要接入钉钉、企业微信等第三方组织数据。
2. LLM 从服务端提供的受信任供应商目录中选择正确的供应商配置。
3. 对话界面展示供应商对应的安全凭据表单。
4. 用户填写钉钉 AppKey/AppSecret，或企业微信 CorpID/Secret。
5. 后端测试连接并保存租户可复用的连接实例。
6. 用户通过聊天选择该连接、同步实体和希沃 MySQL 目标。
7. 用户确认后创建同步任务，复用现有对账、审批、执行、验证、回滚和报告链路。

## 设计结论

采用“通用连接器内核 + 供应商专用 Adapter + 通用 Agent Graph”的方案。

- 新增一个学校连接时，只创建连接配置，不发布代码或 Graph。
- 新增一个符合现有能力合同的供应商时，只增加 Adapter、供应商清单和合同测试。
- 只有同步工作流、节点、证据或安全语义发生变化时，才发布新的 Graph 版本。
- 第一版为通用 API 来源能力发布一次 `agent-sync-graph-v3`。
- `agent-sync-graph-v3` 复用 v2 的节点，不新增钉钉或企微专用节点。
- 在现有 `materialize_sources` 节点下增加
  `capture_api_authority_snapshot` Action。
- 数据接入运行时由“整项任务只有一种来源模式”改为“按 authoritative/target 角色分别解析
  connector kind”，从而明确支持 API 权威来源与数据库目标的混合配对。
- 连接配置与连接测试发生在同步 Graph 外，不获取学校锁，也不创建同步任务。
- AppSecret、Secret 和 access token 不进入聊天记录、LLM 上下文、Skill 输入、MCP 参数、
  Graph checkpoint、报告或普通日志。
- 第三方 API 始终是只读权威来源；希沃 MySQL 是唯一治理目标。

## 目标

- 支持在 AI 对话中选择钉钉和企业微信组织数据来源。
- 根据供应商自动展示正确的凭据字段，不要求用户理解 API 地址和认证流程。
- 连接测试成功后按租户保存并复用连接。
- 允许 `api` 权威来源与 `database` 希沃目标组成一次任务。
- 把一次 API 全量读取冻结为可重放、可审计、内容哈希稳定的不可变来源快照。
- 复用现有 `SourceFile`、`Snapshot`、`RawSnapshotRow`、`CanonicalEntityRecord` 和
  `EntityMapping`。
- 复用现有差异分析、风险审批、目标执行、读后验证、审计、回滚和报告能力。
- 新供应商接入不要求复制 Graph、状态机、审批和执行链路。
- 对认证失败、权限不足、限流、分页异常、部分读取和连接吊销执行失败关闭。
- 保证供应商原始数据中的提示注入文本不能改变 Agent、Graph 或工具权限。

## 非目标

- 不允许 LLM 实时上网搜索 API 文档并生成任意 URL、请求方法或认证代码。
- 不允许用户在普通聊天消息中提交明文 AppSecret 或其他密钥。
- 不提供通用 `raw_http_request`、任意 URL、任意请求头或任意脚本连接器。
- 第一版不支持用户仅靠一份未知 API 文档就动态生成生产连接器。
- 不为钉钉、企微或以后新增的普通供应商复制 Agent Graph。
- 不修改第三方组织数据，不支持双向同步。
- 不保证所有供应商都支持部门、学生、老师三个实体；以经过测试的 Adapter 能力为准。
- 不把钉钉 `userid`、企微 `userid` 或部门 ID 冒充为希沃工号、学号或部门业务编号。
- 不在 API 快照未完整读取时继续差异分析，避免把未读取记录误判为希沃冗余数据。
- 不在本次设计中加入定时调度；连接实例和同步任务必须为未来调度保留稳定引用。

## 需求满足度

| 用户要求 | 设计结论 | 满足情况 |
| --- | --- | --- |
| 在聊天中说明接入钉钉或企微 | 对话 Agent 从服务端供应商目录选择 `provider_id` | 满足 |
| LLM 找到对应配置 | LLM 只能选择版本化、审核过的 Provider Manifest | 满足 |
| 用户只填写必要凭据 | 钉钉显示 AppKey/AppSecret；企微显示 CorpID/Secret | 满足 |
| 填写后自动连接 | 后端提交凭据、认证、检查权限并读取有界样本 | 满足 |
| 连接后创建同步 | 测试成功后生成同步确认卡，用户确认才创建任务 | 满足 |
| 新增学校连接不发布 Graph | 连接实例是租户级数据库记录 | 满足 |
| 新增普通供应商不复制链路 | 新 Adapter 实现同一合同并复用 Graph v3 | 满足 |
| API 数据进入现有对账流程 | API 全量读取固化为现有 Snapshot 和规范实体 | 满足 |
| 希沃暂时由 MySQL 模拟 | API 权威来源与 MySQL 目标配对 | 满足 |
| 支持真实权限和分页问题 | Adapter、快照 Action 和稳定错误码负责 | 满足 |
| 任意未知公司无需开发即可接入 | 第一版明确不支持任意动态 HTTP | 不满足且有意排除 |
| 所有平台天然都有学生数据 | 由供应商能力清单决定，不作虚假承诺 | 条件满足 |

结论：本设计满足钉钉、企业微信等已实现供应商的聊天配置和同步要求，同时有意不满足
“LLM 临时搜索任意 API 并立即执行”的高风险要求。

## 三种方案比较

### 方案一：每个供应商一条 Graph

钉钉、企微分别增加认证、分页、读取、规范化节点。

优点是单个供应商流程直观。缺点是节点、Guard、恢复、测试和版本数量随供应商线性增长，
后续审批和执行链路也容易分叉。本方案不采用。

### 方案二：Graph 不变，在来源检查时直接请求 API

来源检查 sub-agent 运行时直接分页读取第三方 API。

改动看似较少，但同一任务的不同阶段可能读到不同版本的数据；模型重试会重复请求网络；
部分分页失败难以证明快照完整；任务无法可靠重放和审计。本方案不采用。

### 方案三：通用 Graph + 专用 Adapter

Graph 只认识连接器类型、能力、快照和证据；Adapter 负责供应商协议；通用快照 Action 负责
完整分页、固化、哈希和幂等。该方案与现有远程 CSV 的安全边界一致，并能最大程度复用后续
流程。本设计采用该方案。

## 三个不同的版本概念

### Graph 版本

记录工作流节点、Action、Guard 和证据语义，例如 `agent-sync-graph-v3`。只有工作流合同变化
时升级。

### Adapter 版本

记录供应商认证、分页和字段读取实现，例如 `dingtalk-adapter@1.1.0`。供应商 API 调整时只
升级 Adapter，不升级 Graph。

### Provider Manifest 版本

记录凭据字段、能力、权限说明、允许主机和 Adapter 绑定，例如 `dingtalk@1.0.0`。表单或
能力元数据变化时升级 Manifest，不升级 Graph。

任务创建时冻结三者：

```json
{
  "graph_version": "agent-sync-graph-v3",
  "provider_id": "dingtalk",
  "adapter_version": "1.1.0",
  "provider_manifest_version": "1.0.0"
}
```

历史任务始终按已冻结版本恢复。新增连接实例不产生任何新版本。

## 总体职责边界

| 层次 | 负责内容 | 禁止内容 |
| --- | --- | --- |
| 对话 Agent | 理解供应商、实体范围、目标和连接选择 | 接收密钥、访问 API、创建任务 |
| Provider Catalog | 提供审核后的供应商、表单、能力和权限说明 | 接收 LLM 动态生成的 URL |
| 安全配置接口 | 接收凭据、存密钥、测试连接、创建连接实例 | 把密钥写入聊天或日志 |
| API Runtime | 根据连接实例解析 Adapter 和密钥引用 | 把密钥返回给 Agent |
| Provider Adapter | 认证、请求签名、分页和供应商字段读取 | 决定 Graph 流程和治理操作 |
| Graph Action | 完整分页、幂等、快照固化、证据和错误分类 | 理解业务字段语义 |
| Skill/sub-agent | 在授权快照上理解实体和字段语义 | 网络、认证、凭据和任意 HTTP |
| 后续治理链路 | 身份对应、差异、审批、执行、验证和回滚 | 写第三方权威来源 |

## Provider Catalog

供应商配置来自代码仓库中的版本化、受审查文件，不来自模型实时搜索结果。示例：

```yaml
provider_id: dingtalk
version: "1.0.0"
display_name: 钉钉
adapter_key: dingtalk_v1

credential_fields:
  - name: app_key
    label: AppKey
    secret: false
    required: true
  - name: app_secret
    label: AppSecret
    secret: true
    required: true

allowed_hosts:
  - api.dingtalk.com

supported_entities:
  - organization_unit
  - teacher

capabilities:
  read_only: true
  stable_record_id: true
  pagination: true
  full_snapshot: true
```

企业微信 Manifest 使用 `corp_id` 和 `corp_secret`。供应商将来增加新的必要凭据时，升级
Manifest 和 Adapter，不修改 Graph。

Manifest 可以声明审核过的主机、命名端点和表单元数据，但不得携带用户密钥。LLM 只接收
以下安全摘要：

- `provider_id`
- 显示名称和别名
- 支持实体
- 是否已有可用连接
- 安全表单引用
- 脱敏的连接健康状态

LLM 不接收完整端点、请求头、认证响应和供应商原始错误。

## 连接实例与安全配置

### 连接实例

一个连接实例表示某个租户对某个供应商的一组可复用授权：

```text
ConnectorConnection
  id
  tenant_id
  provider_id
  display_name
  adapter_version
  provider_manifest_version
  credential_reference
  status
  capability_summary
  last_tested_at
  created_by
  revoked_at
```

状态限定为：

```text
testing -> active -> invalid | revoked
```

只有 `active` 连接可以进入同步确认卡。连接必须绑定当前可信
`OperatorContext.tenant_id`，客户端和 LLM 都不能提交或覆盖租户。

### 安全配置会话

聊天 Agent 返回 `connector_setup_required` 后，后端创建短期
`ConnectorSetupSession`：

```text
pending -> submitted -> completed | failed | expired
```

会话只保存租户、操作者、对话、供应商、过期时间和状态，不保存密钥。前端根据
`credential_form_id` 在聊天区域渲染安全配置卡。

用户填写的凭据通过专用 HTTPS 接口直接提交给后端。请求不经过对话消息接口，不加入对话
历史，不调用模型，并关闭请求体日志。

### 密钥存储

新增 `CredentialStore` 抽象：

```python
class CredentialStore(Protocol):
    async def put(self, tenant_id: str, values: dict[str, str]) -> str: ...
    async def get(self, tenant_id: str, reference: str) -> dict[str, str]: ...
    async def replace(self, tenant_id: str, reference: str, values: dict[str, str]) -> None: ...
    async def delete(self, tenant_id: str, reference: str) -> None: ...
```

第一版使用应用层 AES-GCM 加密的凭据记录，主密钥只从部署环境注入。连接表只保存
`credential_reference`，不保存明文。该抽象允许生产部署以后替换为 KMS 或 Vault，而不改变
对话、Graph 和 Adapter。

access token 只在 Adapter 的短期内存缓存中保存；过期或进程重启后重新认证，不写数据库、
checkpoint 或日志。

## 聊天交互

### 对话上下文

`ConversationAgentContext` 增加：

```text
available_api_providers
available_api_connections
api_connector_enabled
pending_connector_setup
```

所有字段由服务端生成。连接清单只包含连接 ID、别名、供应商、支持实体和健康状态。

### 对话决策

`ConversationAgentDecision.kind` 增加：

```text
connector_setup_required
connector_connection_selected
```

`connector_setup_required` 只能输出服务端清单中的 `provider_id` 和
`credential_form_id`。它不能输出或接收任何凭据。

连接配置成功后，下一轮上下文会出现新的 `available_api_connection`。当 API 来源、希沃
MySQL 目标和实体范围均确定时，Agent 返回现有 `start_confirmation`，其中来源为：

```json
{
  "kind": "api",
  "configuration_id": "tenant-bound-connection-id"
}
```

用户点击开始后，后端重新校验连接状态、租户、目标角色和实体能力，再创建任务。

### 用户体验示例

```text
用户：接入钉钉，把老师和部门同步到希沃。

Agent：需要先配置钉钉连接。
       [安全配置卡：AppKey、AppSecret]

后端：连接测试成功，已保存“学校钉钉”。

Agent：将使用“学校钉钉”同步老师、部门到“希沃 MySQL”。
       第三方只读，确认后创建全校同步任务。
       [确认开始]
```

如果用户把疑似密钥粘贴到普通聊天，消息处理链必须提醒其轮换密钥，不把疑似值写入模型
历史，并引导使用安全配置卡。

## 通用连接器内核

复用现有 `ConnectorConfiguration`、`ConfiguredApiConnector`、`ConnectorStore`、
`ConnectorPage` 和分页稳定性检查。新增 API 运行时解析器：

```python
class ApiConnectorRuntime:
    async def connector(
        self,
        *,
        tenant_id: str,
        connection_id: UUID,
    ) -> ConfiguredApiConnector: ...
```

解析过程：

1. 按租户读取 `ConnectorConnection`。
2. 校验状态为 `active`。
3. 读取并验证 Provider Manifest 和冻结版本。
4. 从 `CredentialStore` 解析凭据。
5. 根据 `adapter_key` 创建只读 `ConnectorStore`。
6. 用现有 `ConfiguredApiConnector` 包装并执行统一能力检查。

供应商只实现自己的 Store：

```text
DingTalkConnectorStore
WeComConnectorStore
```

Store 负责：

- 获取和刷新 access token。
- 调用审核过的供应商端点。
- 把供应商分页响应转换为 `ConnectorPage`。
- 产生稳定记录 ID、稳定游标和安全版本摘要。
- 把限流、权限、认证和协议错误转换为稳定内部错误码。

Store 不负责：

- 创建任务或选择实体范围。
- 创建 Graph 节点。
- 发布 Snapshot。
- 进行身份匹配或字段差异判断。
- 写入第三方。

### 按角色解析连接器类型

当前 `_task_source_mode()` 把任务整体归类为 `database` 或 `csv`，并让权威来源与目标使用
同一种检查和规范化分支。这不适用于：

```text
authoritative.kind = api
target.kind = database
```

v3 将其替换为按角色解析的不可变配对：

```python
ConnectorPair(
    authoritative=ConnectorBinding(kind="api", configuration_id="..."),
    target=ConnectorBinding(kind="database", configuration_id="..."),
)
```

后续接入阶段分别路由：

```text
authoritative/api
  -> 已固化 API JSONL
  -> API Schema 画像
  -> 权威 CanonicalEntity Snapshot

target/database
  -> 现有数据库 Schema 画像
  -> 参数化、有界读取
  -> 目标 CanonicalEntity Snapshot
```

双方最终都输出现有规范实体和 Snapshot，身份匹配以后不再关心原始 connector kind。该改动
属于接入路由和规范化边界，不改变差异、审批、执行、验证、回滚和报告阶段。

## API 来源快照

### 为什么必须固化

API 分页期间第三方数据可能变化，access token 可能过期，限流和网络也可能导致中途失败。
如果后续阶段继续实时请求 API，同一任务会看到不一致数据，无法证明哪些记录参与了分析。

因此 API 与远程 CSV 一样，在任务内只读取一次，并在后续阶段只使用固化快照。

### 快照 Action

`agent-sync-graph-v3` 在现有 `materialize_sources` 节点增加：

```text
capture_api_authority_snapshot
```

节点结构保持不变：

```text
intent_confirmed
  -> acquire_school_lock
  -> materialize_sources
  -> inspect_sources
  -> normalize_input_batches
  -> validate_input_contract
  -> build_identity_index
  -> 原有分析、审批、执行、验证和报告
```

`materialize_sources` 根据来源类型选择：

```text
remote_csv -> materialize_remote_authority
api        -> capture_api_authority_snapshot
database   -> source_already_stable
local_csv  -> source_already_stable
```

不增加 `dingtalk_node`、`wecom_node`、`refresh_token_node` 或供应商专用 Graph。

### 固化过程

Action 必须确定性完成：

1. 从任务冻结的 `connection_id`、Adapter 版本和实体范围解析连接器。
2. 校验连接仍属于当前租户且未吊销。
3. 对每个声明支持的实体按稳定顺序遍历全部页面。
4. 检查游标不重复、记录 ID 不重复、实体引用合法。
5. 对手机号等敏感字段在进入模型证据前进行现有任务级令牌化。
6. 把原始供应商记录写入确定性 JSONL 管理文件。
7. 计算内容哈希，登记现有 `SourceFile`。
8. 写入 Adapter、Manifest、连接、实体范围、页数、记录数和哈希证据。
9. 保存幂等 checkpoint 后返回 `api-source:materialized` evidence。

Action 到此只完成不可变原始来源物化，不提前做业务语义规范化。后续 `inspect_sources` 和
`normalize_input_batches` 从该 JSONL 生成现有 `Snapshot`、`RawSnapshotRow` 和
`CanonicalEntityRecord`。`Snapshot.source_file_id` 指向 API JSONL 管理文件，不新增 API
专用快照表。来源的 API 类型和版本信息记录在任务意图、快照 summary 和 Action checkpoint
中。

### 完整性和幂等

- 任一实体分页未完整结束时，不登记可用 `SourceFile`，后续也不能发布 Snapshot。
- 部分记录成功、后续页面失败时，整个 Action 失败关闭，不把临时 JSONL 注册为可用
  `SourceFile`。
- 限流只允许按照服务端预算和供应商 `Retry-After` 有界重试。
- 认证过期允许刷新一次 token 后重试当前页。
- 重复游标、重复记录 ID 或不稳定排序视为协议错误。
- checkpoint 已存在且管理文件和哈希一致时，任务恢复直接复用，不重新请求 API。
- checkpoint 存在但文件或哈希不一致时阻断任务，不静默重新拉取。
- 不完整 API 快照不得触发 target-extra、删除或停用类治理建议。

## 规范组织数据与身份对应

### 复用现有 CanonicalEntity

现有规范实体已经包含稳定 `source_id`、组织父级、教师部门、工号、学号、电话和邮箱等字段。
API Adapter 必须生成带命名空间的稳定来源 ID：

```text
api:dingtalk:<connection-id>:organization_unit:<external-dept-id>
api:dingtalk:<connection-id>:teacher:<external-user-id>
api:wecom:<connection-id>:teacher:<external-user-id>
```

这样现有 `NormalizedRecord.record_key = entity_type:source_id` 和 `EntityMapping.source_key`
可以跨任务稳定复用，同时不同租户、供应商和连接的相同外部 ID 不会冲突。

### 不能混淆技术 ID 与业务编号

- 外部 `userid` 只进入 `source_id`，不能填入 `employee_number`。
- 外部部门 ID 只进入组织单元 `source_id` 和父级引用，不能冒充 `code`。
- 只有供应商字段语义明确且经过 Adapter 测试时，才映射工号、学号、手机号和邮箱。
- 姓名、部门和班级只用于业务比较和受控候选上下文，不单独作为自动身份键。

### 实体级最低要求

```text
organization_unit:
  必须：稳定 source_id、name
  可选：code、parent_source_id

teacher:
  必须：稳定 source_id、name
  可选：employee_number、department_source_id、phone、email

student:
  必须：稳定 source_id、name
  可选：student_number、class_source_id、class_name、phone、email
```

手机号、邮箱被供应商权限隐藏时，不把整条老师或学生记录判为无效。是否能够自动对应是另一
个问题。

### 自动对应与人工确认

优先使用现有业务身份键自动对应：

```text
employee_number | student_number | phone_token | email
```

没有共同身份键时进入现有身份冲突人工 Gate，不自动用姓名猜测。人工确认后复用现有
`EntityMapping` 保存来源 `source_key` 与希沃 `target_key` 的确认关系；后续任务直接使用
历史确认映射。

本设计不新增 `external_identity_binding` 表，因为现有 `EntityMapping` 已提供租户隔离、
确认、吊销、替代和跨任务历史映射能力。

## Skill 设计

第一版不新增钉钉 Skill、企微 Skill、OAuth Skill 或 HTTP Skill。认证、分页、限流、重试、
主机白名单和快照固化全部属于确定性代码。

### 升级对话 Skill

`converse-school-data-sync` 升级主要版本，支持：

- 服务端提供的 API Provider 和连接清单。
- `connector_setup_required` 决策。
- `api` 权威来源与 `database` 希沃目标组合。
- 供应商能力不足时的单项澄清。
- 不接收、不复述、不请求聊天中的凭据。

### 升级来源检查 Skill

`inspect-external-data-source` 已声明支持 API，但需要升级合同：

- 只检查已物化 API Snapshot，不直接访问网络。
- 验证 Adapter、Manifest、稳定来源 ID、完整分页和快照版本证据。
- 按实体最低要求判断结构，不再要求每个实体都具备固定六字段。

### 升级规范化 Skill

`normalize-organization-data-batch` 升级主要版本：

- 输出与现有 `CanonicalEntity` 对齐的实体级字段。
- 保留带命名空间的 `source_id` 和关系引用。
- 电话只接收任务级 token。
- 缺少可选身份键时保留记录并进入人工对应，而不是直接排除。
- 仍然禁止从目标记录反向补造第三方权威字段。

### 何时才新增 API Schema Skill

只有未来允许接入“没有专用 Adapter、Schema 未知但已由后端安全物化”的受控 API 时，才
考虑新增 `understand-organization-api-schema`。该能力不在第一版范围内。

## 数据模型

### 新增

```text
connector_connections
  id
  tenant_id
  provider_id
  display_name
  adapter_version
  provider_manifest_version
  credential_reference
  status
  capability_summary
  last_tested_at
  created_by
  revoked_at

connector_setup_sessions
  id
  tenant_id
  operator_id
  conversation_id
  provider_id
  credential_form_id
  status
  expires_at

connector_credentials
  id
  tenant_id
  ciphertext
  nonce
  key_version
  created_at
  rotated_at
```

`connector_credentials` 只能通过 `CredentialStore` 访问，不向业务仓储或 API Schema 暴露
密文结构。

### 复用

- `ReconciliationTask.agent_intent`：冻结 API 连接、Provider、Adapter、Manifest 和实体范围。
- `SourceFile`：保存确定性 API JSONL 管理文件及内容哈希。
- `Snapshot`：保存任务级权威快照。
- `RawSnapshotRow`：保存供应商记录的受控原始结构。
- `CanonicalEntityRecord`：保存规范组织实体。
- `EntityMapping`：保存自动或人工确认的跨来源身份对应。
- Agent checkpoint、evidence manifest 和 invocation audit：保存 Graph 执行证据。

## 后端接口

建议新增以下租户绑定接口：

```http
GET    /api/agent/connector-providers
POST   /api/agent/connector-setup-sessions
POST   /api/agent/connector-setup-sessions/{id}/credentials
GET    /api/agent/connector-connections
POST   /api/agent/connector-connections/{id}/test
POST   /api/agent/connector-connections/{id}/rotate
DELETE /api/agent/connector-connections/{id}
```

接口规则：

- Provider 列表不返回端点、密钥和内部 Adapter 类名。
- 凭据提交成功后立即进行连接测试；只有测试通过才创建 `active` 连接。
- 测试结果只返回支持实体、权限状态、脱敏组织摘要和安全错误码。
- `rotate` 使用新的安全配置会话，成功后原子替换密钥引用。
- `DELETE` 表示吊销连接和删除凭据；已有任务快照继续可审计，不能用该连接启动新任务。
- 所有 ID 必须与当前可信租户和操作者授权绑定。

现有任务创建合同允许：

```json
{
  "source": {
    "kind": "api",
    "configuration_id": "connection-id"
  },
  "target": {
    "kind": "database",
    "configuration_id": "seewo-mysql-id"
  }
}
```

任务创建服务必须重新验证来源只读、目标可写、实体能力和连接健康，不信任对话模型的判断。

## Agent Graph 变化

### 版本

新增一次 `agent-sync-graph-v3`：

- 节点集合与 v2 保持一致。
- `materialize_sources` 增加 `capture_api_authority_snapshot` Action。
- Action 候选根据冻结的明确 `source.kind` 生成。
- 不能再把所有非数据库来源默认解释为 CSV。
- v1、v2 历史任务继续使用原定义。

### 不随连接或供应商变化

以下操作不发布新 Graph：

- 某学校新增或轮换钉钉密钥。
- 同一学校新增第二个钉钉连接。
- 某学校新增企业微信连接。
- 钉钉端点或分页参数调整并升级 Adapter。
- 新增一个实现相同分页快照合同的普通供应商 Adapter。

### 需要新 Graph 的情况

只有出现新的工作流语义才发布后续版本，例如：

- 供应商必须先提交异步导出任务，持久等待回调后再下载。
- 同步任务需要新的人工授权 Gate。
- 引入增量游标并改变快照或恢复语义。
- 开放反向写第三方或双向冲突解决。
- 改变执行、审批或回滚阶段。

## 错误处理

使用稳定、脱敏错误码：

```text
api_invalid_credentials
api_insufficient_scope
api_connection_revoked
api_token_refresh_failed
api_rate_limited
api_request_timeout
api_pagination_cursor_repeated
api_record_id_missing
api_record_id_duplicated
api_partial_snapshot
api_entity_unsupported
api_schema_unsupported
api_snapshot_hash_mismatch
credential_store_unavailable
```

错误规则：

- 连接测试错误不创建连接实例。
- 认证和权限错误引导用户重新配置或在供应商后台授权。
- 限流和超时使用有界重试，预算耗尽后暂停或安全失败。
- 供应商原始错误正文、请求 ID、URL 查询参数和响应内容不进入用户消息。
- 部分快照绝不进入后续对账。
- 已有活动任务继续遵守学校锁，不允许通过聊天配置新连接绕过锁并创建第二个同步任务。
- 目标写入阶段仍使用现有目标版本、审批、幂等和回滚规则。

## 安全要求

- 普通聊天接口拒绝或遮蔽疑似凭据。
- LLM、Skill、MCP 和 Graph Supervisor 永远没有 `read_secret` 权限。
- 不提供任意 HTTP 工具。
- Adapter 只能访问 Manifest 中审核过的 HTTPS 主机。
- 禁止重定向到未允许主机、内网、环回、本地文件或其他协议。
- 请求和响应日志默认不记录请求体、Authorization 和 access token。
- 所有供应商字段和记录内容都视为不可信数据，不能改变工具调用和 Graph 候选。
- 学生手机号在模型可见前使用现有任务级令牌化。
- 连接、凭据、配置会话和快照都执行租户隔离。
- 第三方 Adapter 不实现写入能力；`ConfiguredApiConnector.apply` 对权威来源继续失败关闭。
- 连接吊销后删除或不可恢复地失效凭据引用。
- 先前在普通聊天或日志中暴露过的真实密钥必须轮换后才能用于正式测试。

## 可观测性与审计

连接级审计事件：

```text
connector_setup_started
connector_test_succeeded
connector_test_failed
connector_credential_rotated
connector_revoked
```

任务级审计事件：

```text
api_snapshot_started
api_page_captured
api_snapshot_published
api_snapshot_failed
api_snapshot_reused_from_checkpoint
```

审计允许记录：

- tenant、operator、conversation、connection 和 task 的内部引用。
- Provider、Adapter、Manifest 和 Graph 版本。
- 实体类型、页数、记录数、耗时、重试次数和内容哈希。
- 稳定安全错误码。

审计禁止记录：

- AppSecret、Secret、access token 和完整认证响应。
- 原始学生手机号。
- Authorization、Cookie 和含密钥查询参数。
- 供应商响应中的任意提示文本。

## 测试策略

### 单元测试

- Provider Manifest Schema、版本和主机白名单。
- DingTalk 和 WeCom 认证请求构造。
- access token 过期刷新。
- 单页、多页、空页和结束游标。
- 重复游标和重复记录 ID。
- 权限不足、限流、超时和供应商异常映射。
- 连接租户隔离和状态转换。
- 凭据加密、轮换、删除和日志脱敏。
- 外部 ID 命名空间和长度限制。
- 实体级最低字段要求。

### Adapter 合同测试

所有 Provider Adapter 必须通过同一测试套件：

- 只读能力。
- 稳定 `version`。
- 稳定记录 ID。
- 有界分页。
- 游标单调推进且不会重复。
- 完整读取结束信号。
- 安全错误转换。
- 不泄露凭据。
- 不产生供应商专用 Graph Action。

新增普通供应商时，只需运行该合同测试和自己的响应 fixture 测试。

### 集成测试

使用本地模拟 HTTP 服务和合成组织数据，不使用真实教师、学生和手机号：

- 安全配置卡提交后创建 active 连接。
- 无效密钥不创建连接。
- `api + database` 任务创建成功。
- API 快照完整分页后发布现有 Snapshot。
- 中途失败不发布 Snapshot。
- worker 重启后复用 checkpoint，不重复调用 API。
- API 快照进入现有规范化、身份对应和差异生成。
- 无共同身份键进入人工确认。
- 人工确认的 `EntityMapping` 在下一任务复用。
- 第三方记录中的提示注入不能扩大权限。

### Graph 测试

- v3 节点集合与 v2 保持一致。
- API 来源只出现 `capture_api_authority_snapshot` 候选。
- 远程 CSV 仍只出现 `materialize_remote_authority`。
- 数据库和本地 CSV 不调用 API Action。
- 新增测试 Provider 不改变 Graph 定义。
- v1、v2 历史运行仍能恢复。
- API Action evidence 和 checkpoint 哈希可重放。

### 前端和端到端测试

- 聊天识别钉钉或企微并展示对应安全表单。
- 密钥不出现在聊天消息 DOM、历史接口和任务详情。
- 连接成功后可被当前租户选择。
- 连接失败显示安全恢复动作。
- 确认卡展示第三方只读、希沃目标和实体范围。
- 一次确认只创建一个任务。
- 连接吊销后不能创建新任务。

### 真实环境烟雾测试

真实测试默认跳过，只在显式提供测试租户和密钥的隔离环境运行：

- 获取 token。
- 读取部门第一页。
- 读取成员第一页。
- 验证权限范围和稳定 ID。
- 不保存或打印真实成员明细。

真实凭据不得进入仓库、CI fixture、截图和测试报告。

## 开发顺序

### 第一阶段：连接器控制面

- Provider Catalog 和 Manifest Schema。
- `ConnectorConnection`、`ConnectorSetupSession` 和 `CredentialStore`。
- 安全配置接口和连接测试。
- 对话上下文、决策 Schema 和安全配置卡。

### 第二阶段：钉钉纵向切片

- `DingTalkConnectorStore`。
- 认证、部门、成员、分页和安全错误。
- 连接测试与 Adapter 合同测试。

### 第三阶段：Graph API 快照

- `agent-sync-graph-v3`。
- `capture_api_authority_snapshot` Action、Guard、evidence 和 checkpoint。
- API JSONL `SourceFile` 与现有 Snapshot 发布。
- `api + database` 任务配对。

### 第四阶段：规范化和身份对应

- 对齐现有 CanonicalEntity。
- 升级来源检查与规范化 Skill。
- 命名空间 `source_id`。
- 无共同身份键的人工确认和历史 `EntityMapping` 复用。

### 第五阶段：企业微信

- `WeComConnectorStore`。
- 复用同一 Catalog、连接、Graph、快照和合同测试。
- 不创建企业微信专用 Graph 节点。

### 第六阶段：安全与发布

- 全量自动化测试和迁移烟雾测试。
- 日志与错误脱敏检查。
- 租户隔离和提示注入测试。
- 按 Provider 启用开关逐步发布。

## 配置与发布

复用现有 `new_agent_api_connector_enabled` 作为总开关，并增加供应商级开关：

```text
api_connector_dingtalk_enabled
api_connector_wecom_enabled
```

关闭总开关时：

- 对话 Agent 不显示 API Provider 或连接。
- 配置接口拒绝新建和测试。
- 已完成任务和历史快照仍可查看。

关闭单个供应商时：

- 不允许新建、测试或使用该供应商连接。
- 不影响其他供应商。
- 已经固化的任务快照继续进入后续安全阶段。

## 对现有代码的影响

主要修改：

- `backend/app/schemas/agent_conversation.py`
  - API Provider、连接和配置会话上下文。
  - 新的对话决策类型。
- `backend/app/api/routes/agent.py`
  - Provider、配置会话、凭据提交、连接测试、轮换和吊销接口。
- `backend/app/agent_runtime/task_service.py`
  - 允许只读 API 权威来源与 MySQL 数据库目标。
- `backend/app/agent_graph/definition.py`
  - 注册 `agent-sync-graph-v3`，节点集合保持不变。
- `backend/app/agent_graph/runtime.py`
  - API 来源 Action 候选和明确来源模式。
- `backend/app/agent_graph/production_executor.py`
  - API 来源物化 Action、按角色接入路由和后续现有 Snapshot 发布。
- `backend/app/connectors/configured.py`
  - 复用并补充 API 只读能力合同。
- `backend/app/connectors/api_runtime.py`
  - 连接实例、Manifest、密钥和 Adapter 解析。
- `backend/app/connectors/providers/dingtalk.py`
  - 钉钉认证、部门、成员和分页。
- `backend/app/connectors/providers/wecom.py`
  - 企微认证、部门、成员和分页。
- `backend/app/ai/skills/contracts.py`
  - API 来源和实体级规范合同。
- `backend/app/ai/skills/converse-school-data-sync/SKILL.md`
  - API 供应商和安全配置决策。
- `backend/app/ai/skills/inspect-external-data-source/SKILL.md`
  - 已固化 API 来源检查。
- `backend/app/ai/skills/normalize-organization-data-batch/SKILL.md`
  - 与 CanonicalEntity 对齐的规范化规则。

新增数据库迁移只包含连接、配置会话和加密凭据控制面。API 快照和身份映射复用现有表。

## 验收标准

以下条件全部满足才认为功能完成：

1. 用户说“接入钉钉”时，系统从服务端目录选择钉钉并展示 AppKey/AppSecret 安全表单。
2. 用户说“接入企业微信”时，系统展示 CorpID/Secret 安全表单。
3. 明文密钥不进入聊天历史、模型请求、Skill 输入、MCP 参数、Graph checkpoint 和日志。
4. 有效凭据测试成功后创建当前租户可复用的 active 连接。
5. 无效凭据、权限不足或能力不支持时不创建可用连接。
6. 用户选择 active API 连接和希沃 MySQL 后可以生成开始确认卡。
7. 用户确认后只创建一个 `api + database` 同步任务。
8. Graph 使用 `agent-sync-graph-v3`，节点数量不因钉钉或企微增加。
9. API 来源只增加通用 `capture_api_authority_snapshot` Action。
10. 运行时按角色解析 `api` 权威来源和 `database` 目标，不再依赖单一任务来源模式。
11. API 全量分页完整结束后才登记原始来源，并在规范化后发布不可变 Snapshot。
12. 部分页失败、重复游标、重复 ID 或连接吊销时不进入差异分析。
13. worker 重启或 Action 重试不会再次拉取已经成功固化且哈希一致的 API 数据。
14. 外部技术 ID 进入带命名空间的 `source_id`，不冒充工号、学号或部门业务编号。
15. 手机、邮箱等可选字段不可见时记录仍可进入规范化，但不会无证据自动匹配。
16. 无共同身份键时进入人工确认，确认结果通过现有 `EntityMapping` 在后续任务复用。
17. 差异分析、风险审批、目标执行、验证、审计、回滚和报告继续使用现有链路。
18. 新增一个通过合同测试的模拟 Provider 时不修改 Graph 定义。
19. 新增第二个学校连接时只新增连接记录，不发布应用或 Graph。
20. 第三方始终只读，所有可写操作仍只作用于希沃 MySQL 目标。
21. 后端、前端、迁移、Graph、Adapter、安全和端到端测试全部通过。

## 最终评估

该设计能够满足“在聊天中选择钉钉或企业微信、只填写对应凭据、完成连接测试并创建同步”的
开发目标，同时避免以下不可维护或不安全的结果：

- 每个学校连接发布一条新 Graph。
- 每个供应商复制一套对账和治理链路。
- LLM 自由搜索并调用未知 API。
- 密钥进入聊天和模型上下文。
- API 分页不完整却继续产生删除类差异。
- 把供应商技术 ID 当成希沃业务编号。

实现范围仍然较大，但边界清晰，可拆成连接器控制面、钉钉纵向切片、通用 API 快照、
规范化与身份对应、企业微信五个独立交付阶段。最关键的架构判断是：Graph 只为通用 API
来源能力升级一次；以后新增连接实例或符合合同的普通供应商均不发布新链路。
