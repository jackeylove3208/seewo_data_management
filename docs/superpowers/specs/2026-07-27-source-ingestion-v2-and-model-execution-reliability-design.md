# 数据接入 V2 与模型执行可靠性设计

## 背景

当前 `agent-graph-v1` 的数据接入流程存在两个主要问题：

1. `inspect-external-data-source` 让模型判断 CSV 是否可识别，但文件存在性、编码、表头、
   权限和解析能力大多可以由固定程序可靠判断。
2. `normalize-organization-data-batch` 每批最多五十条调用模型完成固定六字段映射和规范化，
   导致数据接入耗时长、模型调用多，并容易因输出覆盖不完整或 JSON 合同偏差而失败。

当前数据库连接器虽然已有服务端配置、稳定分页和参数化写入等基础能力，但仍以单表和固定
字段列配置为主，且 durable Agent runtime 尚未把数据库连接器完整接入同步状态图。

治理和回滚阶段还存在另一类可靠性问题：模型需要调用确定性执行工具，并把服务端已经产生的
操作结果重新输出为严格 JSON。模型只要遗漏操作、改变状态或未完整调用工具，就会连续四次
失败并显示：

```text
模型分析已暂停
本阶段共进行了 4 次模型尝试。模型输出未通过结构化结果校验；
任务数据和学校锁仍被安全保留。
```

本设计同时解决数据接入架构和治理/回滚模型失败问题。

## 已确认的产品决策

- 本次继续只支持三种实体：部门、学生、教师。
- 本次继续使用固定字段：`category`、`name`、`number`、`class_name`、`phone`、`email`。
- `class_name` 只适用于学生，教师和部门保持为空。
- 本次不开发动态字段、家长、家庭住址、教龄等扩展字段。
- 数据源只允许两种同类组合：
  - 第三方 CSV 对希沃 CSV。
  - 第三方 SQL 对希沃 SQL。
- 不支持 CSV 与 SQL 混合任务。
- 手动同步入口继续只支持 CSV 对 CSV。
- 对话入口由 Agent 识别用户选择的是 CSV 模式还是 SQL 模式。
- 第三方权威数据始终只读。
- 希沃是唯一允许被修改的一方，同时必须具备读取权限，用于对账、写前版本检查、读后验证和
  回滚冲突判断。
- SQL 第一版正式支持 MySQL，连接器接口为后续 PostgreSQL、SQL Server 保留扩展边界。
- 模型不得生成或执行任意 SQL、Shell、路径、URL 或凭据。
- 数据接入不再逐行或逐批调用模型。
- 治理执行和回滚执行不再要求模型复述服务端执行事实。
- 现有历史任务继续按创建时的 workflow version 和 Skill version 恢复，不修改历史语义。

## 目标

- 保留现有 CSV 对账能力，并显著缩短数据接入时间。
- 让标准 CSV 数据接入阶段在正常情况下不调用模型。
- 支持第三方 MySQL 与希沃 MySQL 的完整读取、对账、治理、验证、报告和回滚链路。
- 让模型只处理字段语义或表关系存在歧义的部分。
- 继续输出当前固定三实体六字段合同，避免本次改动扩散为动态字段重构。
- 消除治理和回滚中不必要的模型执行中转，避免确定性操作因模型 JSON 偏差而失败。
- 保留有用且可恢复的模型失败审计，使前端能显示真正的失败阶段和类别。

## 非目标

- 不支持 CSV 与 SQL 混合输入。
- 不支持 API 数据源。
- 不支持任意用户提交数据库 DSN、账号、密码或 SQL。
- 不允许模型直接查询数据库或直接修改希沃。
- 不改变当前三实体六字段业务合同。
- 不新增动态字段目录、动态字段值表或动态治理策略。
- 不修改第三方数据。
- 不扩大登录、学校切换或角色权限系统。
- 不改变学校范围锁、审批、风险、幂等、审计和回滚的服务端所有权。

## 总体架构

```text
对话 Agent / 手动同步
  -> 形成同类数据源意图
  -> 服务端验证数据源与连接器权限
  -> 确定性探测数据源
  -> 确定性发现字段、表和稳定定位
  -> 复用已有映射或条件式调用结构理解 Skill
  -> 服务端校验并冻结固定六字段映射
  -> 确定性批量读取和规范化
  -> 确定性数据质量校验
  -> 保存现有 AgentInputRecord
  -> 进入身份索引和对账分析
```

### 权责边界

#### 对话 Agent

- 从完整对话上下文识别用户要使用 CSV 模式还是 SQL 模式。
- 识别用户提到的服务端连接器别名或已上传 CSV。
- 信息不足时提出一次明确问题。
- 只能输出来源意图，不能自行连接数据库、读取本机任意路径或创建任务写入参数。

#### 结构理解 sub-agent

- 只在固定规则无法完成字段或表映射时调用。
- 只能读取 evidence manifest 中的字段画像、表画像、脱敏样本和候选关系。
- 只能把已有物理字段映射到固定六字段合同。
- 不能创造第七个业务字段，不能生成 SQL，不能决定写入权限。

#### 确定性后端

- 验证数据源组合、租户、学校锁、连接器能力和权限。
- 探测 CSV 或 SQL Schema。
- 生成字段候选、表候选和关联候选。
- 校验并冻结模型映射草案。
- 编译读取计划和目标写入计划。
- 批量读取、规范化、标记异常、保存快照。
- 编译、执行和验证希沃治理操作。
- 保存审计、报告事实和回滚事实。

## 数据源意图

新增版本化意图合同 `AgentSourcePairIntentV2`：

```json
{
  "mode": "csv",
  "authoritative": {
    "kind": "csv",
    "resource_id": "uploaded-authoritative-id"
  },
  "target": {
    "kind": "csv",
    "resource_id": "authorized-target-id"
  }
}
```

或：

```json
{
  "mode": "sql",
  "authoritative": {
    "kind": "database",
    "connector_id": "education-authority-mysql"
  },
  "target": {
    "kind": "database",
    "connector_id": "seewo-target-mysql"
  }
}
```

服务端必须拒绝以下意图：

```text
authoritative=csv, target=database
authoritative=database, target=csv
mode=csv 但缺少两个 CSV 资源
mode=sql 但连接器未在服务端配置
第三方连接器拥有写能力
希沃连接器缺少读取、写入或验证能力
```

对话 Agent 遇到混合请求时不能自行转换数据源，必须提示用户在 CSV 对 CSV 和 SQL 对 SQL 之间
选择。尚未形成合法来源意图前，不创建同步任务、不获取学校锁。

## CSV 对 CSV 接入

### 确定性探测

后端直接完成：

- 文件是否存在且属于上传资源或授权目录。
- 文件大小是否符合限制。
- 编码、BOM、分隔符和换行格式识别。
- 表头是否存在且无重复空字段。
- CSV 是否可以稳定重放。
- 物理行号是否可以作为稳定顺序。
- 希沃目标文件是否位于允许写回的授权目录。

这些判断不调用模型。

### 固定字段映射

后端先使用现有字段别名和类型规则映射：

```text
category / 类别 / entity_type / 实体类型
name / 姓名 / 名称
number / 编号 / id / 工号 / 学号
class / class_name / 班级
phone / 电话 / 手机号
email / 邮箱 / 电子邮箱
```

如果双方表头都能唯一映射，直接冻结映射，不调用模型。

如果存在陌生表头或一个表头对应多个候选，条件式调用
`map-csv-organization-schema`。该 Skill 只接收：

- 双方 CSV 表头。
- 后端推测的数据类型。
- 空值率、唯一率和脱敏格式特征。
- 后端生成的固定字段候选。
- 当前三实体六字段合同。

输出只允许：

```json
{
  "schema_version": "fixed-six-field-mapping-v2",
  "authoritative_mappings": [
    {
      "source_field_ref": "csv-column:学籍号码",
      "contract_field": "number",
      "entity_kinds": ["student"],
      "normalizer_id": "trim_identifier"
    }
  ],
  "target_mappings": [],
  "unresolved_required_fields": []
}
```

服务端必须验证引用、唯一性、实体适用范围和 normalizer 白名单。

### 批量读取和规范化

映射冻结后，由后端按较大批次读取 CSV，不再每五十条调用模型。规范化继续复用确定性逻辑：

- 类别映射为 `department`、`student`、`teacher`。
- 文本去除首尾空格。
- 编号按字符串保存，不能转换后丢失前导零。
- 手机号、邮箱和空值使用现有规范化规则。
- `class_name` 只写入学生。
- 保存物理行号、稳定定位、稳定顺序和输入哈希。
- 学生手机号继续使用现有隐私和令牌化边界。

### 数据质量校验

继续保持当前固定合同：

- 第三方部门和教师要求 `category`、`name`、`number`、`phone`、`email`。
- 第三方学生额外要求 `class_name`。
- 不完整第三方记录只标记、排除并进入报告，不修改第三方。
- 希沃记录中 `number`、`phone`、`email` 都为空时，保留为目标异常证据。
- 输入阶段不直接删除或修改希沃记录。

## SQL 对 SQL 接入

### 连接器配置

连接器由服务端配置，客户端和模型只看到 `connector_id`。配置至少包含：

```text
connector_id
dialect=mysql
credential_ref
allowed_database
allowed_tables_or_views
source_role
access_mode
version_strategy
```

权限要求：

- 第三方权威连接器为只读账号，后端还要做能力验证，发现写权限时拒绝任务。
- 希沃目标连接器是唯一可写端，同时具备读取和事务能力。
- 凭据只能从服务端 secret reference 解析。
- 不允许用户文本覆盖连接器 ID 对应的数据库、账号或权限。

### 确定性数据库探测

后端通过 SQLAlchemy/MySQL metadata 和受控查询读取：

- 数据库版本和字符集。
- 白名单表、视图和字段。
- 字段类型、长度、是否可空、默认值和注释。
- 主键、唯一索引和普通索引。
- 外键和后端计算的候选关联路径。
- 稳定主键和稳定分页能力。
- 行数、空值率、唯一率和脱敏样本格式。
- 希沃目标的事务、版本检查和读后验证能力。

该阶段不调用模型，也不把任意 SQL 工具暴露给模型。

### 数据库结构理解 Skill

当不存在可复用的数据库映射方案时，调用
`understand-organization-database-schema`。

该 Skill 同时接收第三方和希沃的有界 Schema 画像，以便一次性把双方映射到同一固定合同。
它负责：

- 选择学生、教师、部门对应的根表或受控视图。
- 把物理列映射到固定六字段。
- 从后端提供的候选关系中选择必要连接。
- 选择稳定主键。
- 标记无法映射的必填字段。

输出示例：

```json
{
  "schema_version": "fixed-six-field-sql-mapping-v2",
  "authoritative_entities": [
    {
      "entity_kind": "student",
      "root_relation_ref": "relation:authority.students",
      "stable_key_ref": "field:authority.students.id",
      "field_bindings": {
        "category": "constant:student",
        "name": "field:authority.students.full_name",
        "number": "field:authority.students.student_code",
        "class_name": "field:authority.classes.class_name",
        "phone": "field:authority.students.mobile",
        "email": "field:authority.students.email"
      },
      "join_candidate_refs": [
        "join:authority.students.class_id->authority.classes.id"
      ]
    }
  ],
  "target_entities": [],
  "unresolved_required_fields": []
}
```

模型只能使用清单中的 relation、field 和 join candidate ID，不能返回 SQL 文本。

### 映射校验和编译

服务端在冻结映射前必须验证：

- 所有表、字段和关联引用真实存在且属于白名单。
- 三种实体均存在稳定根表和稳定主键。
- 连接路径无循环、无笛卡尔积，深度不超过配置上限。
- 根实体在连接后仍保持一行一个实体。
- 字段类型与固定合同兼容。
- `class_name` 只用于学生。
- 必填权威字段均可投影。
- 希沃字段绑定只能指向目标连接器。
- 目标字段在服务端写入白名单中。

校验通过后，由后端编译 SQLAlchemy 查询表达式。模型输出不能直接成为数据库查询。

### 确定性抽取

- 使用稳定主键顺序分页，每页建议五百至一千条。
- 抽取结果继续保存到现有 `AgentInputRecord` 固定字段。
- `stable_locator` 使用连接器 ID、实体类型和稳定主键组成的不可歧义引用。
- 保存映射版本、Schema 指纹和源版本。
- 数据质量规则与 CSV 模式一致。

### Schema 指纹与映射缓存

映射缓存键至少包含：

```text
authoritative_connector_id
authoritative_schema_fingerprint
target_connector_id
target_schema_fingerprint
skill_version
contract_version
```

双方 Schema 指纹都未变化时直接复用映射，SQL 数据接入阶段不调用模型。

任何表、字段、类型、主键、唯一约束或使用中的关系发生变化时，映射失效并重新进入结构理解
阶段。不能在旧映射上静默运行。

## 数据接入状态图 V2

```text
source_intent_pending
  -> validate_source_pair
  -> acquire_school_lock
  -> probe_source_pair
  -> discover_source_pair_schema
  -> resolve_fixed_field_mapping
     -> reuse_mapping
     -> map_csv_schema             [仅 CSV 表头存在歧义]
     -> understand_database_schema [仅 SQL 新 Schema 或映射失效]
  -> validate_and_freeze_mapping
  -> extract_authoritative_snapshot
  -> extract_target_snapshot
  -> validate_source_snapshots
     -> abnormal_input_report      [整体无法满足固定合同]
     -> build_identity_index       [输入可继续]
```

固定的连接探测、映射校验、批量抽取和快照校验应建模为 deterministic 节点，不应包装成只有
唯一动作的 Supervisor 决策。

用户终止时停止启动新的读取批次，安全结束当前读取单元，保存已完成的接入统计并生成终止报告。

## Skill 与接入步骤变更

| 现有或新增组件 | 新工作流处理 | 原因 |
|---|---|---|
| `inspect-external-data-source` | 不再调用，仅保留旧任务兼容 | 文件、连接、权限和 Schema 探测应由确定性代码完成 |
| `normalize-organization-data-batch` | 不再调用，仅保留旧任务兼容 | 固定六字段规范化可以由可测试的后端函数完成 |
| `map-csv-organization-schema` | 条件式新增 | 只处理无法由别名规则确定的 CSV 表头 |
| `understand-organization-database-schema` | 条件式新增 | 只处理新 MySQL Schema 的表、字段和关系映射 |
| `validate_normalized_input` | 由 `validate_source_snapshots` 取代 | 扩展为真正检查映射、快照、稳定定位和数据质量的确定性节点 |

旧 Skill 不能立即删除。历史 `agent-graph-v1` 任务仍按已冻结的 Skill 名称和版本恢复。新任务
进入 `source-ingestion-v2` 后不再调用旧 Skill；确认没有需要恢复的旧运行后才能归档旧实现。

## 固定字段兼容

本次不改变 `AgentInputRecord`、身份键和普通字段比较的业务范围：

```text
department: category, name, number, phone, email
teacher:    category, name, number, phone, email
student:    category, name, number, class_name, phone, email
```

因此：

- 现有编号、手机号、邮箱身份 posting 继续使用。
- 现有普通字段差异检测继续使用固定字段集合。
- 现有 finding、审批卡片和报告字段标签继续可用。
- CSV 治理继续使用固定字段写回。
- SQL 治理通过冻结的固定字段目标绑定写入对应列。
- 不引入动态字段数据库迁移。

## SQL 治理、验证与回滚

### 操作编译

AI 分析和治理方案只能引用固定合同字段。服务端根据冻结映射将语义字段编译为希沃目标列：

```text
student.phone
  -> connector=seewo-target-mysql
  -> table=students
  -> primary_key=id
  -> column=mobile
```

模型不能决定真实表名、列名和 SQL。

### 写前检查

- 任务仍持有本校排他锁。
- 操作属于当前冻结计划。
- 审批和风险版本仍有效。
- 目标行稳定主键存在。
- 当前值或版本仍等于计划中的 `before`。
- 目标列属于写入白名单。

### 写入和验证

- 使用参数化 SQLAlchemy 表达式和数据库事务。
- 使用稳定幂等键。
- 写入后在同一连接器中读取目标值。
- 只有实际值等于计划 `after` 才记录 `succeeded`。
- 版本冲突不能通过重试覆盖。
- 单项失败阻断其依赖项，独立操作继续。

### 回滚

- 每次回滚仍是新的排他任务。
- 补偿操作只来自原任务验证成功的 mutation。
- 使用原执行时冻结的连接器、表、主键、列、before、after 和版本证据。
- 当前目标值发生漂移时进入冲突处理，不能覆盖新数据。
- 回滚结果生成独立报告和历史记录。

## 四次模型尝试失败的调查结论

### 已确认现场

最近一次可复现记录为：

```text
task_id: 0ff52aa5-734e-46f4-b4a5-ed35446675d6
failed_node: execute_restore_operations
attempt_count: 4
failure_categories: [model_output_failure]
```

同一个 `deepseek-v4-flash` 模型在该任务中成功完成了回滚影响评估，并在终止后成功生成报告。
因此这次错误不是单纯的模型服务不可用，也不是把四次提高到更多次数就能解决的问题。

### 根因一：模型承担了不必要的确定性执行中转

治理和回滚执行当前要求模型：

1. 读取冻结计划。
2. 调用服务端执行工具。
3. 覆盖每一个 operation ID。
4. 把服务端结果原样输出为严格合同。

服务端随后要求模型输出与真实执行结果逐项完全相等。模型遗漏一个 ID、未调用完工具或改变
一个状态，就会被整体判为 `model_output_failure`。

这一步没有业务判断价值。写入参数、顺序、审批、版本、幂等和验证已经由服务端冻结，模型
只是把机器事实重新抄写一次，却成为任务成功的必要条件。

### 根因二：四次重试没有获得可执行的修复信息

普通业务校验 `ValueError` 当前被压缩为：

```json
{
  "path": "$",
  "type": "ValueError"
}
```

模型无法知道实际错误是：

- 缺少哪个 operation ID。
- 哪个结果与服务端事实不一致。
- 哪个执行工具没有调用。
- 是否超过工具调用上限。

后续尝试接近重复第一次请求，增加重试次数不会稳定修复。

### 根因三：失败审计可能被业务事务回滚

回滚执行的模型 invocation、工具调用和目标操作处于同一个外围事务。模型最终失败导致外围
事务回滚时，四次 invocation 明细也可能一起消失，只剩 worker 在新事务中保存的汇总失败。

这使后台无法向前端解释具体失败合同，也妨碍根因追踪。

### 根因四：`json_object` 只保证 JSON，不保证业务合同

当前 DeepSeek 使用 `json_object` 模式。后端把 JSON Schema 和示例写进提示词，但供应商只
保证返回 JSON 对象，不保证结果通过 Pydantic、成员覆盖和服务端事实一致性校验。

这会增加结构偏差概率，但它是放大因素，不是主要根因。

## 模型执行可靠性新设计

### 核心调整

治理执行和回滚执行改为：

```text
Supervisor 选择服务端 allowed_action
  -> 服务端校验图 guard、锁、审批、版本和计划
  -> 确定性批量执行器执行操作
  -> 服务端读后验证
  -> 服务端直接持久化结构化结果
  -> Supervisor 根据服务端事实选择继续、报告或终止
```

模型继续负责：

- 对账异常分析。
- 生成治理方案。
- 解释需要人工决定的冲突。
- 在存在真实多个允许动作时由 Supervisor 选择。
- 根据冻结事实生成中文报告。
- 回滚前影响分析。

模型不再负责：

- 请求执行已经批准的每一项操作。
- 复述服务端执行结果。
- 决定某个写入是否实际成功。
- 生成回滚写入参数。

### 治理执行节点

以下节点改为 deterministic：

```text
compile_execution_plan
preflight_execution
execute_ready_operations
verify_operations
execute_remaining_independent
```

Supervisor 可以选择合法的“执行”“等待”“终止”“重新规划”等动作，但一旦选择执行，具体
操作由服务端批量执行器完成。

### 回滚执行节点

以下节点改为 deterministic：

```text
compile_restore_plan
preflight_restore
execute_restore_operations
verify_restore_operations
```

`assess_restore_impact` 仍可使用模型，因为它需要生成面向用户的影响分析；实际补偿操作和验证
不经过模型。

### 失败记录事务边界

模型 invocation 审计必须与调用它的业务事务分离：

- 调用开始记录在独立短事务。
- 每次失败状态和安全诊断记录在独立短事务。
- 完成状态记录在独立短事务。
- 业务事务回滚不能删除模型失败审计。
- 不持久化原始提示词、原始模型输出、凭据或原始学生手机号。

### 可修复错误合同

模型仍可能用于分析、映射、Supervisor 和报告。结构校验失败时保存机器可读错误码：

```text
missing_required_field
unknown_field
invalid_field_type
unknown_evidence_ref
missing_member_id
duplicate_member_id
candidate_outside_manifest
tool_argument_outside_manifest
tool_call_budget_exceeded
```

修复请求在内存中包含：

- 上一次模型 JSON。
- 具体字段路径。
- 安全错误码。
- 允许成员列表的哈希和缺失成员 ID。
- 仍然适用的 JSON Schema。

审计只保存哈希和安全错误码，不保存敏感原文。

### 重试策略

保留最多四次总尝试的安全上限，但按错误类型处理：

| 错误类型 | 处理 |
|---|---|
| 超时、429、502、503、504 | 使用退避进行有界重试 |
| JSON/Pydantic 结构错误 | 携带精确字段路径和上一次输出进行修复 |
| 证据成员覆盖错误 | 携带缺失或重复成员 ID 进行修复 |
| 工具参数不属于证据清单 | 拒绝当前参数并提供允许资源范围 |
| 服务端 guard、版本或审批失败 | 不调用模型重试，立即进入对应服务端状态 |
| 确定性执行或验证失败 | 按连接器操作重试策略处理，不消耗模型尝试 |

四次是上限，不是每种错误都必须运行四次。不可修复的安全错误应立即失败关闭。

### 前端错误表达

前端不再统一显示“结构化结果校验失败”，应根据安全类别显示：

```text
模型服务暂时不可用
模型返回格式不符合当前阶段合同
模型遗漏了本批必须覆盖的数据项
模型请求了当前阶段未授权的数据工具
受控数据工具执行失败
目标数据版本发生变化
治理写入失败
治理写入后的验证结果不一致
```

事件必须包含：

```text
failed_node
business_stage
attempt_count
safe_failure_category
allowed_commands
```

不得在前端显示供应商原文、堆栈、凭据、绝对路径或未脱敏数据。

## 工作流版本和兼容

建议新增数据接入合同版本：

```text
source-ingestion-v2
fixed-six-field-mapping-v2
```

可继续沿用 `agent-graph-v1` 图版本，也可以在实现时根据状态图兼容性升级为新的 graph version。
无论选择哪种方式，都必须满足：

- 已创建任务永久绑定创建时的 workflow、graph、Skill 和 mapping 版本。
- 旧任务仍可读取、终止、报告和回滚。
- 新任务不再调用 `inspect-external-data-source` 和
  `normalize-organization-data-batch`。
- 数据库任务只在双方都是已配置 SQL 连接器时创建。
- 新功能开关不得改变历史任务语义。

建议新增功能开关：

```text
RECONCILIATION_SOURCE_INGESTION_V2_ENABLED=false
RECONCILIATION_AGENT_GRAPH_SQL_EXECUTION_ENABLED=false
```

SQL 执行开关关闭时允许完成连接诊断和映射预览，但不能创建声称可治理的正式 SQL 同步任务。

## 实施边界

### 可以复用

- 现有 `AgentInputRecord` 固定六字段模型。
- 现有 `AgentContractMapper` 字段别名和规范化规则。
- 现有身份 posting、work item 和固定字段差异逻辑。
- 现有 evidence manifest、Skill registry、MCP 权限和工具审计。
- 现有学校锁、lease、fencing、审批、计划、幂等、报告和回滚模型。
- 现有 SQLAlchemy 参数化连接器基础和版本检查思想。
- 现有 CSV 原子发布与授权目录边界。

### 需要重构

- 对话任务意图增加 CSV/SQL 同类来源模式。
- durable Agent runtime 真正绑定数据库连接器。
- 数据库连接器从单表固定列扩展为三实体多表投影配置。
- 数据接入图替换旧两个模型 Skill。
- 新增 CSV 条件式字段映射 Skill。
- 新增 SQL 数据库结构理解 Skill。
- 新增映射缓存、Schema 指纹和冻结投影计划。
- 新增 SQL 固定字段读取编译器和希沃目标写入适配器。
- 治理与回滚执行移除模型中转。
- 模型失败审计改为独立事务和明确安全错误码。

## 测试要求

### CSV 数据接入

- 标准中文六字段 CSV 对 CSV 全流程不调用模型。
- 标准英文别名 CSV 对 CSV 全流程不调用模型。
- 陌生表头只调用一次 CSV 字段映射 Skill，不逐批调用模型。
- Skill 映射缺少必填字段时进入异常输入报告。
- CSV 对 SQL 请求在任务创建前被拒绝。
- SQL 对 CSV 请求在任务创建前被拒绝。
- 旧 CSV 任务仍可恢复和报告。

### SQL 数据接入

- 第三方只读 MySQL 与希沃读写 MySQL 可以完成三实体固定字段抽取。
- 第三方连接器检测到写权限时拒绝任务。
- 希沃缺少读取、写入、事务或读后验证能力时拒绝正式任务。
- 模型返回不存在的表、列或关系引用时被服务端拒绝。
- 多表连接产生重复根实体时映射校验失败。
- 相同 Schema 指纹复用映射且不调用模型。
- Schema 漂移使旧映射失效。
- 分页重跑保持相同稳定顺序和定位。

### SQL 治理和回滚

- 固定字段更新使用参数化 SQL。
- 删除和学生手机号修改继续进入高风险审批。
- 中风险操作继续按现有审批分组策略展示。
- 写前版本漂移阻止修改。
- 写入成功但读后值不一致记录为验证失败。
- 部分失败继续独立操作。
- 回滚只使用原任务验证成功的 mutation。
- 回滚遇到目标漂移时不覆盖新值。

### 四次模型失败修复

- 含五十个操作的治理执行不调用模型，并能保存逐项结果。
- 含多个补偿操作的回滚执行不调用模型，不再出现
  `model_output_failure`。
- Supervisor 只选择服务端 allowed action，不能构造执行参数。
- 分析 Skill 第一份 JSON 无效、第二份修复成功时任务继续。
- 修复请求包含明确安全错误路径，审计不包含原始模型输出。
- 四次失败记录即使业务事务回滚仍然存在。
- 前端显示真实失败节点、业务阶段和错误类别。
- 模型提供商不可用与模型业务合同失败显示不同提示。

### 质量门

实现完成后至少运行：

```bash
cd backend
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/mypy app

cd ../frontend
npm test -- --run
npm run lint
npm run typecheck
npm run build
```

启用 MySQL 集成测试时还要验证：

- 干净数据库建表和映射。
- 只读账号权限。
- 事务回滚。
- 乐观并发冲突。
- 读后验证。
- 重启后的幂等恢复。

测试只能使用合成学校数据和临时数据库，不能使用真实学生、教师或家长数据。

## 验收标准

1. 新建任务只接受 CSV/CSV 或 SQL/SQL，不接受混合来源。
2. 手动同步只提供 CSV/CSV；对话 Agent 可以识别 CSV 或 SQL 模式。
3. 标准 CSV 接入全程不调用模型。
4. CSV 陌生表头只在映射阶段调用模型，不逐批调用。
5. SQL 新 Schema 只在结构映射阶段调用模型；相同指纹后续复用。
6. 新任务仍只产生三种实体和固定六字段 `AgentInputRecord`。
7. 第三方永远不能成为写入目标。
8. 希沃是唯一写入目标，并执行写前检查和读后验证。
9. 治理和回滚执行不依赖模型复述服务器事实。
10. 六个及以上回滚操作不会因为模型工具调用或结果覆盖问题进入四次失败。
11. 模型失败审计不因业务事务回滚而丢失。
12. 前端能区分模型服务、模型合同、证据、工具、版本和目标写入失败。
13. 历史任务仍能读取、终止、报告和回滚。

## 设计结论

数据接入 V2 不再把固定且可测试的工作交给 LLM：

- `inspect-external-data-source` 由确定性 CSV/SQL 探测替代。
- `normalize-organization-data-batch` 由确定性批量抽取和规范化替代。
- AI 只在 CSV 表头存在歧义或 SQL 表/字段/关系首次映射时工作。
- 本次映射目标仍严格限制为三实体六字段，不引入动态字段。

治理和回滚也采用同一原则：

- AI 负责分析、方案、冲突解释、Supervisor 选择和报告叙述。
- 后端负责计划、审批、版本、写入、验证、执行结果和回滚事实。
- 四次模型失败上限保留，但不再让不需要模型的确定性执行消耗模型尝试。
