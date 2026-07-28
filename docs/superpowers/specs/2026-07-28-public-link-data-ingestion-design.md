# 公共网页链接数据接入设计

## 背景

当前项目的数据接入主要依赖本地上传。对于希沃侧数据，这种方式仍然合理，因为希沃数据是
对账目标，且后续治理、验证和回滚需要一个稳定、可审计的目标快照。

对于第三方权威数据，要求用户先下载再上传会带来以下问题：

- 用户需要手工搬运数据，流程长且容易上传旧版本。
- 第三方数据可能只通过公开下载地址持续提供，本地文件并不是它的自然交付方式。
- 原始来源、下载时间、重定向链和内容哈希难以形成完整审计证据。
- 如果把任意网址直接交给模型或 MCP，现有的任务授权、资源边界和安全控制会被绕开。

本设计只为 AI 对话入口增加“公共网页链接”接入方式。只有用户在当前对话消息中发送链接时，
系统才会激活该能力。第一版只支持无需登录的 HTTPS 直链 CSV；希沃目标侧继续使用现有受控
本地资源。手动同步入口、合同和页面保持不变。

## 已确认的产品与架构决策

- 用户可以在 AI 对话消息中提交一个公开 HTTPS CSV 下载地址作为第三方权威数据来源。
- 只有对话消息处理链可以登记 `remote_csv`；手动同步不得创建或启动这种来源。
- 用户提交的网址先由确定性后端登记，不直接进入任务提示词、MCP 参数或模型上下文。
- 同步任务只引用租户绑定的 `remote_source_id`。
- Graph 新增可恢复的资源固化 Action，把远程内容下载一次并固化为不可变 `SourceFile`。
- MCP 只允许读取当前任务 evidence manifest 已授权的固化资源，不接受任意 `url`。
- 文件格式、编码、分隔符、大小和可解析性由确定性程序判断。
- sub-agent 只负责理解组织数据的业务语义，例如实体类型和固定六字段映射。
- 模型不得负责网络下载，不得决定网络安全策略，也不得读取完整原始文件。
- 第三方权威数据始终只读。
- 每个任务使用固定内容哈希的快照；任务重试和恢复时不重新下载。
- 新任务使用新的 Graph/workflow 版本，历史任务继续按创建时的旧版本恢复。

## 目标

- 让用户通过 AI 对话中的公共 CSV 链接接入第三方权威数据，无需先下载到本地。
- 保持手动同步当前行为、请求合同和界面不变。
- 保留来源网址、下载时间、最终响应元数据和内容哈希等可审计证据。
- 让网络访问、模型分析和业务执行分别处于明确的安全边界。
- 复用现有 `SourceFile`、evidence manifest、Skill 版本固定、MCP 授权和 CSV 规范化能力。
- 保证超时、失败、重试、服务重启和浏览器断开后任务仍可安全恢复。
- 保持现有部门、学生、教师三类实体及固定六字段业务合同不变。

## 非目标

- 不抓取普通 HTML 页面，也不让模型从网页视觉内容中寻找下载按钮。
- 不支持需要登录、Cookie、验证码、浏览器会话、Token 或自定义请求头的地址。
- 不支持 HTTP、FTP、本地文件路径、内网地址或用户提供的任意 API 调用。
- 第一版不支持 Excel、JSON、压缩包、多文件目录或流式 API。
- 不支持定时刷新或让运行中的任务自动跟随远程内容变化。
- 不修改第三方数据。
- 不把希沃目标改为远程网页数据源。
- 不在手动同步页面增加链接输入框，也不让 `/api/agent/tasks` 接受远程来源。
- 不扩大当前三实体六字段合同，也不引入动态字段。
- 不允许模型生成或执行 URL、SQL、Shell、路径或凭据。

## 为什么采用 Action、MCP、Skill 三层结构

三者处理的问题不同，不能由一个 sub-agent 同时承担：

| 层次 | 负责内容 | 不负责内容 |
| --- | --- | --- |
| Graph Action | 下载、校验、固化、重试、幂等、审计 | 业务字段语义 |
| MCP 工具 | 向已授权 sub-agent 提供有界、脱敏、可追溯的资源视图 | 任意网络访问 |
| Skill/sub-agent | 识别实体与字段业务含义，输出严格结构化结论 | 下载、格式判定、权限与执行 |

如果让 sub-agent 直接接收 URL 并通过通用 MCP 下载，会破坏现有
`url` 禁止参数和 evidence manifest 授权边界，也难以保证 DNS、重定向、下载大小、内容变化
和任务重放安全。

因此，远程下载是一个确定性的 Graph Action；MCP 是受控读取通道；Skill 是业务语义判断器。

## 总体流程

```text
用户在 AI 对话中发送公开 CSV 链接
  -> 对话消息处理链确定性提取并登记 remote_source_id
  -> 将链接替换为安全来源摘要后再调用对话模型
  -> 创建任务，意图只保存 remote_source_id
  -> 获取学校范围锁
  -> materialize_sources 节点
       -> 校验 URL 和每次重定向
       -> 有界下载并计算内容哈希
       -> 确定性识别 CSV 格式
       -> 固化为不可变 SourceFile
       -> 写入来源和 Action checkpoint
  -> inspect_sources 节点
       -> 读取确定性字段画像
       -> 仅在业务语义存在歧义时启动 sub-agent
       -> sub-agent 通过 MCP 读取脱敏、有界样本
       -> 后端校验并冻结字段映射
  -> 复用现有批量规范化、数据质量校验、对账、审批、治理、验证和报告流程
```

本设计中的“网页链接”是用户入口概念。进入任务以后，它会被转换为受控远程资源，不再作为
模型可操作的网络地址存在。

## 对话触发与任务意图

### 对话消息中的远程来源登记

不新增供手动同步或通用客户端调用的远程来源登记接口。现有对话消息接口仍只接收：

```http
POST /api/agent/conversations/{conversation_id}/messages
Content-Type: application/json
```

```json
{
  "message": "请用 https://data.example.edu/export/organization.csv 和希沃数据核对学生"
}
```

消息处理链在调用对话模型前确定性完成：

- 从本轮用户消息提取最多一个 HTTP(S) URL。
- 没有 URL 时完全沿用现有对话行为。
- URL 合法时登记租户、操作者和会话绑定的 `remote_source_id`。
- 在发给对话模型的 `message` 和 `history` 中，用
  `[远程CSV来源:data.example.edu]` 替换完整 URL。
- 把 `remote_source_id` 和安全来源摘要作为服务端事实提供给对话模型。
- 实际下载仍属于同步 Graph，不在消息请求中执行。

一条消息包含多个 URL、非 HTTPS URL、嵌入凭据或明显非法地址时，消息接口返回安全澄清，
不登记来源、不创建任务，也不调用模型猜测应选择哪个链接。

完整原始 URL 属于受保护的远程来源记录：

- 只在服务端保存。
- 数据库字段和备份按项目敏感配置要求保护。
- 日志、模型提示词、MCP 参数、报告和前端错误不显示完整 URL 或查询参数。
- 对话历史和前端只显示经过清理的域名和安全摘要。

远程来源必须绑定创建它的 `conversation_id`。开始任务时，后端同时校验租户、操作者、
对话和来源状态，防止复制内部 ID 到其他对话。

### 来源意图

在现有 CSV 模式内增加远程权威来源类型：

```json
{
  "mode": "csv",
  "authoritative": {
    "kind": "remote_csv",
    "remote_source_id": "remote-source-uuid"
  },
  "target": {
    "kind": "local",
    "source_ref": "seewo/organization.csv"
  }
}
```

`remote_csv` 是 CSV 的一种传输方式，不引入 CSV 与 SQL 混合模式。后端必须验证：

- `remote_source_id` 属于当前租户和当前操作者可用范围。
- `remote_source_id` 属于当前对话，且由该对话中的用户消息登记。
- 资源尚未绑定其他租户。
- 权威侧为只读。
- 希沃目标满足现有读取、治理、验证和回滚要求。

只有对话 Agent 可以生成含 `remote_csv` 的意图。通用任务创建服务在处理该类型时必须要求
非空且匹配的 `conversation_id`；手动任务接口即使伪造 `remote_source_id` 也必须在创建任务
和获取学校锁之前拒绝。

## Graph 与 Action 设计

### Graph 版本

新增 `agent-sync-graph-v2`，流程调整为：

```text
intent_confirmed
  -> acquire_school_lock
  -> materialize_sources
  -> inspect_sources
  -> normalize_input_batches
  -> validate_input_contract
  -> 后续现有流程
```

不直接修改 `agent-sync-graph-v1` 的节点语义。历史任务必须继续使用它们创建时固定的 Graph、
Skill 和合同版本恢复，避免旧 checkpoint 在新节点定义下产生歧义。

### `materialize_sources` 节点

对于现有本地或已上传 CSV，资源已经固化，该节点直接确认资源可用。对于对话任务中的
`remote_csv`，生成
`materialize_remote_authority` Action。

Action 输入只包含内部 ID：

```json
{
  "action_kind": "materialize_remote_authority",
  "remote_source_id": "remote-source-uuid",
  "source_role": "authoritative"
}
```

执行结果引用不可变资源：

```json
{
  "status": "succeeded",
  "source_file_id": "source-file-uuid",
  "sha256": "content-sha256",
  "byte_size": 123456,
  "media_type": "text/csv",
  "retrieved_at": "2026-07-28T10:00:00Z"
}
```

Action 必须具备：

- 持久化 checkpoint。
- 按任务、节点、Action kind 和来源 ID 构造的幂等键。
- 可区分的安全失败码。
- 对取消、超时、进程重启和重试的恢复能力。
- 与现有学校锁释放和任务终态规则一致的异常处理。

### 不可变快照

远程内容成功固化后，本任务的所有后续阶段只读取同一个 `SourceFile`：

- 重试不重新请求远程地址。
- 浏览器断开不影响已启动的下载和后续任务。
- 远程地址后来返回新内容，不改变当前任务。
- 报告和回滚证据使用固化内容哈希。
- 用户需要获取新版本时，显式登记或刷新为新的远程来源快照并创建新任务。

如果进程在下载完成但 checkpoint 提交前中断，恢复逻辑应根据幂等键和已保存哈希复用完整
`SourceFile`；未完成的临时对象不能作为输入资源。

## 远程下载安全边界

远程资源固化器必须是专用服务，不复用允许任意请求的通用 HTTP 客户端接口。

### URL 和网络策略

- 只允许 HTTPS。
- 禁止 URL 内嵌用户名或密码。
- 禁止 IP 字面量地址。
- 初始域名及每次重定向都重新执行安全校验。
- 拒绝环回、私网、链路本地、多播、保留地址和云元数据地址。
- 限制重定向次数，建议第一版最多三次。
- 拒绝 HTTPS 降级到 HTTP。
- DNS 校验必须约束实际连接目标，不能只校验首次解析结果，防止 DNS rebinding。
- 设置连接、首字节、读取和总时长限制。
- 不携带用户 Cookie、Authorization、自定义 Header 或浏览器会话。

### 内容策略

- 在读取正文前检查 `Content-Length`，超限直接拒绝。
- 流式读取时再次累计字节，超过现有上传大小上限立即停止。
- 只接受可按 CSV 解析的文本内容，不能只信任服务端声明的 MIME。
- HTML 登录页、错误页、压缩包、Excel 和 JSON 返回明确的安全失败码。
- 识别 BOM、编码、换行符和分隔符，并保存确定性探测结果。
- 表头为空、重复、结构不稳定或解析错误时，不进入模型阶段。
- 下载中的所有内容都视为不可信输入。

### 隐私和提示注入

- 完整 URL 和查询参数不写入普通日志。
- 原始手机号、邮箱等敏感值不进入模型上下文；沿用现有令牌化和脱敏规则。
- 远程单元格中的文字只作为数据证据，不能被解释为给模型的指令。
- MCP 返回固定结构和有界样本，Skill 明确忽略数据中的命令、提示或角色声明。
- 错误报告只包含安全问题码、清理后的来源域名和可操作建议。

## MCP 资源和工具设计

现有 Graph MCP 网关继续负责校验：

- 租户、任务、运行、Graph 节点和 Action 绑定。
- evidence manifest 允许的工具和资源。
- Skill 版本及当前调用阶段。
- 分页上限、敏感字段令牌化和审计记录。

建议增加或扩展两个业务工具：

```text
inspect_remote_source(resource_id)
read_connector_page(resource_id, page_locator, limit)
```

其中 `resource_id` 实际指向已经固化的 `SourceFile`，不是远程 URL。

`inspect_remote_source` 返回：

- 服务端确认的 CSV 格式和编码。
- 表头及稳定字段引用。
- 行数、空值率、唯一率和类型画像。
- 三实体六字段的确定性候选。
- 脱敏样本页定位符。
- 可供输出引用的 evidence refs。

`read_connector_page` 返回：

- 最大五十条的有界记录。
- 稳定行定位符。
- 脱敏或令牌化后的值。
- 下一页定位符。

工具必须拒绝：

- `url`、`path`、`dsn`、`sql`、凭据或任意请求参数。
- 非当前任务、租户、节点或 evidence manifest 的资源。
- 超过限制的分页和样本数量。
- 对第三方来源的任何写操作。

MCP 不是下载器。它只把已固化、已授权的证据投影给 sub-agent。

## Skill 与 sub-agent 设计

### 放置位置

新增版本化 Skill，建议名称和目录：

```text
backend/app/ai/skills/understand-remote-organization-source/SKILL.md
```

它属于现有 Agent 数据接入 Skill 集合，而不是顶层用户 Action。Graph Action 是运行时工作单元，
Skill 是该工作单元需要语义判断时使用的模型合同。

第一版保持独立 Skill，便于明确远程不可信内容、允许工具和证据边界。后续如果它与
`map-csv-organization-schema` 的输入输出完全收敛，可以合并合同，但不应在首版提前耦合。

### 调用条件

以下情况不调用 sub-agent：

- 文件不是合法 CSV。
- 现有别名和类型规则可以唯一映射全部必要字段。
- 后端已经有同一内容哈希和合同版本的有效映射。

只有存在陌生表头、多候选字段或实体含义歧义时才启动 sub-agent。

### 允许能力

Skill 只允许调用：

```text
inspect_remote_source
read_connector_page
submit_input_contract_verdict
```

它可以：

- 判断数据是否包含部门、学生或教师记录。
- 把已有物理字段映射到 `category`、`name`、`number`、`class_name`、`phone`、`email`。
- 为映射引用 evidence refs。
- 报告无法可靠解决的必需字段。

它不能：

- 发起网络请求或看到原始 URL。
- 判断网络地址是否安全。
- 改变服务端确认的文件格式。
- 创造第七个业务字段。
- 输出原始手机号、邮箱或其他敏感样本。
- 修改第三方或希沃数据。

### 输出合同

建议新增 `RemoteSourceUnderstandingV1`：

```json
{
  "schema_version": "remote-source-understanding-v1",
  "recognized": true,
  "entity_kinds": ["department", "student", "teacher"],
  "mappings": [
    {
      "source_field_ref": "csv-column:学籍号码",
      "contract_field": "number",
      "entity_kinds": ["student"],
      "normalizer_id": "trim_identifier",
      "evidence_refs": ["profile:学籍号码", "sample-page:1"]
    }
  ],
  "unresolved_required_fields": [],
  "safe_problem_codes": []
}
```

后端必须验证：

- `source_field_ref` 存在于固化表头。
- 一个物理字段和合同字段之间没有冲突映射。
- `contract_field`、`entity_kinds` 和 `normalizer_id` 均在白名单内。
- evidence refs 属于当前 evidence manifest。
- 输出不包含额外字段或原始敏感值。

只有通过后端校验的结果才能冻结为现有固定六字段映射 checkpoint。

## 数据模型建议

新增远程来源记录，概念字段如下：

```text
id
tenant_id
created_by
state = registered | materializing | ready | failed
protected_original_url
display_origin
source_file_id
content_sha256
byte_size
media_type
retrieved_at
safe_problem_code
created_at
updated_at
```

具体命名可在实现计划中结合现有 SQLAlchemy 模型确定，但必须满足：

- 原始 URL 与普通业务字段隔离。
- `source_file_id` 只在完整固化后关联。
- 状态变化具备审计时间。
- 失败信息不保存响应正文、凭据或完整查询参数。
- 删除或过期策略不能破坏仍被任务和审计记录引用的快照。

## 状态、失败和恢复语义

### 对话登记阶段失败

URL 语法、协议或显式凭据不合法时，在任务创建前返回可读的 `422` 问题详情，并保留用户已填写
的对话上下文。多个 URL 返回澄清，不选择其中任何一个。没有 URL 的普通聊天不进入远程来源
逻辑。

### 固化阶段失败

网络解析、重定向、超时、内容超限、内容类型或 CSV 解析失败时：

- Action 写入安全问题码和 checkpoint。
- 可重试故障按现有重试策略执行。
- 不可重试故障进入输入异常报告。
- 不创建可被后续阶段读取的半成品 `SourceFile`。
- 任务终止时按现有规则释放学校锁。

建议问题码：

```text
remote_source_dns_rejected
remote_source_redirect_rejected
remote_source_timeout
remote_source_too_large
remote_source_unsupported_content
remote_source_invalid_csv
remote_source_changed_during_transfer
remote_source_unavailable
```

### 语义识别失败

模型输出不符合合同、引用不存在字段或无法识别必需字段时：

- 保留模型尝试和工具审计。
- 按现有结构化输出重试上限处理。
- 不进入规范化和对账阶段。
- 向用户报告缺失字段或不确定映射，不暴露样本原文。

## 审计与可观测性

每个成功远程来源至少记录：

- 远程来源 ID、租户和操作者。
- 清理后的来源域名。
- 请求开始和完成时间。
- 重定向次数及安全校验结果，不记录敏感 URL。
- 字节数、媒体类型、编码、分隔符和内容哈希。
- 固化 `SourceFile` ID。
- Graph、Action、Skill 和输出合同版本。
- 是否调用 sub-agent、调用原因和 evidence manifest ID。
- 最终冻结映射的来源：确定性规则、缓存或 sub-agent。

监控指标应区分登记失败、网络策略拒绝、远端不可用、内容无效、模型合同失败和后续业务数据
质量失败，避免把所有问题归类为“模型失败”。

## 测试策略

### 单元测试

- HTTPS、嵌入凭据、IP 字面量和禁止网段规则。
- DNS 结果变化及重定向逐跳校验。
- HTTP 降级、重定向上限、超时和响应大小限制。
- MIME 与实际内容不一致、HTML、Excel、JSON、压缩包和非法 CSV。
- 编码、BOM、分隔符、重复表头和稳定行定位。
- Action 幂等恢复、内容哈希复用和半成品隔离。
- URL、查询参数和响应正文的日志脱敏。

### MCP 与 Skill 合同测试

- MCP 拒绝 `url`、`path`、`dsn`、`sql` 和凭据参数。
- MCP 拒绝错误租户、任务、节点、Action 和 evidence manifest 的资源。
- 分页数量上限及手机号、邮箱令牌化。
- Skill 忽略单元格中的提示注入文字。
- Skill 输出不存在字段、额外合同字段、非法 normalizer 或原始敏感值时被拒绝。
- 无歧义映射不调用模型。

### 集成测试

- `agent-sync-graph-v2` 从远程固化进入探测、映射、规范化和对账。
- 下载完成前进程中断后可以安全重试。
- 下载完成但 checkpoint 未提交时复用同一完整快照。
- 远程地址在任务执行后改变，不影响当前任务重放。
- 固化失败形成输入异常报告并释放学校锁。
- 只有对话入口能生成远程来源意图。
- 手动任务接口伪造 `remote_csv` 时在创建任务和获取学校锁前失败。
- `agent-sync-graph-v1` 历史任务仍按旧节点恢复。

### 端到端测试

- 用户在 AI 对话中粘贴公共 CSV 链接并确认希沃目标后可看到明确进度。
- 手动同步页面没有链接控件，现有 CSV 上传行为不变。
- 非法链接、超限文件、HTML 页面和失效链接显示安全、可操作的错误。
- 前端、日志、报告和模型审计中不出现完整 URL、查询参数或未脱敏样本。
- 成功任务显示来源域名、获取时间和内容哈希摘要。

所有测试只使用本地模拟 HTTP 服务和合成组织数据，不访问真实学校或第三方数据。

## 分阶段交付建议

### 第一阶段：受控公共 CSV

- 对话消息中登记 `remote_source_id`。
- `agent-sync-graph-v2` 和资源固化 Action。
- HTTPS/SSRF/重定向/大小/超时/CSV 校验。
- 不可变 `SourceFile` 和来源审计。
- MCP 受控资源读取。
- 条件式远程组织数据理解 Skill。
- 对话消息触发、链接脱敏和会话绑定。
- 手动同步远程来源拒绝规则。

### 第二阶段：运营与复用

- 允许用户显式刷新为新快照。
- 按内容哈希和合同版本安全复用字段映射。
- 增加来源可用性和失败类型监控。
- 完善过期与保留策略。

### 后续独立提案

需要登录的网页、API 凭据、浏览器抓取、定时同步、Excel/JSON 和多文件来源应分别评审。它们的
授权、隐私、刷新和解析语义与公共 CSV 不同，不应通过放宽本设计的 URL 或 MCP 边界实现。

## 验收标准

本设计实现完成后应满足：

1. 用户只有在 AI 对话中发送公开 HTTPS CSV 链接时才能创建第三方权威数据任务。
2. 任意 URL 不会进入 MCP 工具参数或模型上下文。
3. Graph 在语义识别前生成可审计、不可变、可恢复的远程快照。
4. 文件格式判断完全由确定性后端完成。
5. 只有字段业务语义存在歧义时才调用专用 sub-agent。
6. sub-agent 只能读取当前任务授权的有界、脱敏证据。
7. 任务重试和历史恢复不会因为远端内容变化而改变输入。
8. 网络失败、内容失败和语义失败具有不同的安全问题码和用户提示。
9. 第三方数据保持只读，现有审批、治理、验证和回滚边界不变。
10. `agent-sync-graph-v1` 历史任务恢复语义不变。
11. 手动同步页面和合同不增加远程链接能力，伪造的手动远程来源请求在加锁前被拒绝。

## 实施前仍需在计划中落实的细节

以下内容不改变本设计方向，但需要在实施计划中根据现有代码和配置给出精确落点：

- 复用现有上传大小上限的具体配置项。
- `RemoteSource` 与现有 `SourceFile` 模型、保留策略和迁移的精确字段。
- 新 Graph 节点、Action executor、MCP registry 和 Pydantic 合同的具体模块。
- 对话消息中的安全来源摘要及确认卡展示方式。
- 本地模拟 HTTPS 服务和 DNS 安全测试夹具的实现方式。

这些是实现落点，不是产品决策；在本设计获得确认后进入实施计划。
