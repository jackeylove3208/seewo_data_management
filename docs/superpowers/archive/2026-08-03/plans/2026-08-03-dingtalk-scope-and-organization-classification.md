# DingTalk Scope and Organization Classification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 仅在当前钉钉同步意图中显示连接卡片，前端提供“部门、人员、全部”，并按钉钉行政单元层级将人员稳定归类为教师或学生。

**Architecture:** 下游继续使用 `department`、`teacher`、`student`，不新增 `person` 实体。人员/全部在连接测试时读取安全的部门树，由版本化 Skill 分类分支，后端校验继承关系并冻结映射；任务采集只复用映射，不再次调用模型。

**Tech Stack:** Python 3.12、FastAPI、Pydantic v2、SQLAlchemy、httpx、pytest、React、TypeScript、Vitest。

## Global Constraints

- 设计依据：`docs/superpowers/specs/2026-08-03-dingtalk-scope-and-organization-classification-design.md`。
- 模型输入只能包含部门 ID、名称、父 ID 和路径；不得包含凭据、人员资料或接口原文。
- `department_entity_kinds` 与分类审计信息由服务端生成，并从 API 安全视图中过滤。
- 部门范围不调用模型，也不要求人员目录读取权限。
- 新连接使用 `sync_scope`；历史 `person_entity_kind` 连接继续支持任务重放。
- 不新增数据库迁移，不新增通用 `person` 实体。
- 每个任务遵循红灯测试、最小实现、绿灯验证、独立提交。

---

## Task 1: 建立同步范围配置合同

**Files:**

- Create: `backend/app/api_connectors/dingtalk_configuration.py`
- Modify: `backend/app/api_connectors/service.py`
- Test: `backend/tests/unit/api_connectors/test_dingtalk_configuration.py`
- Test: `backend/tests/integration/api/test_api_connectors.py`

**Interfaces:**

```python
DingTalkSyncScope = Literal["department", "people", "all"]

def validate_new_task_configuration(
    configuration: Mapping[str, object],
) -> dict[str, object]: ...

def entity_kinds_for_scope(
    configuration: Mapping[str, object], *, allow_legacy: bool = False
) -> tuple[AgentEntityKind, ...]: ...

def redact_server_configuration(
    configuration: Mapping[str, object],
) -> dict[str, object]: ...
```

- [ ] **Step 1: 写失败测试**

覆盖以下规则：

```text
department -> department
people     -> teacher, student
all        -> department, teacher, student
```

同时断言：人员/全部必须携带 `person_classification_mode=organization_unit_llm`；新请求中的 `person_entity_kind`、`class_name_field`、`department_entity_kinds`、`organization_classification` 返回 422；历史配置可在 `allow_legacy=True` 时解析。

- [ ] **Step 2: 运行红灯测试**

```bash
cd backend
.venv/bin/pytest tests/unit/api_connectors/test_dingtalk_configuration.py tests/integration/api/test_api_connectors.py -q
```

Expected: 新合同测试失败。

- [ ] **Step 3: 实现合同并接入服务**

在新模块中定义三种范围、范围到实体的唯一映射和服务端字段集合：

```python
SERVER_CONFIGURATION_KEYS = frozenset(
    {"department_entity_kinds", "organization_classification"}
)
```

`ApiConnectionService.create/rotate` 只接受新合同；`_safe_view` 对钉钉调用 `redact_server_configuration`。内部连接记录仍保留完整映射供任务绑定。

- [ ] **Step 4: 验证并提交**

```bash
cd backend
.venv/bin/pytest tests/unit/api_connectors/test_dingtalk_configuration.py tests/integration/api/test_api_connectors.py -q
git add app/api_connectors/dingtalk_configuration.py app/api_connectors/service.py tests/unit/api_connectors/test_dingtalk_configuration.py tests/integration/api/test_api_connectors.py
git commit -m "feat: define DingTalk synchronization scopes"
```

Expected: 测试通过且安全视图不返回服务端分类字段。

---

## Task 2: 读取安全的钉钉组织快照

**Files:**

- Modify: `backend/app/api_connectors/contracts.py`
- Modify: `backend/app/api_connectors/dingtalk.py`
- Test: `backend/tests/contract/test_organization_api_adapters.py`

**Interfaces:**

```python
class OrganizationUnitNode(BaseModel):
    department_id: str
    name: str
    parent_id: str | None
    path: tuple[str, ...]

class OrganizationInspection(BaseModel):
    departments: tuple[OrganizationUnitNode, ...]
    personnel_department_ids: frozenset[str]
    personnel_memberships: tuple[tuple[str, ...], ...]
    visible_person_count: int
    tree_fingerprint: str

async def inspect_organization(
    public_configuration: Mapping[str, object],
    secret: Mapping[str, str],
) -> OrganizationInspection: ...
```

- [ ] **Step 1: 写失败合同测试**

使用“学校→教职工→数学组、学校→学生→七年级→一班”的合成树，断言节点唯一、路径正确、指纹稳定，返回值不含用户 ID、姓名、手机、邮箱；分页异常和权限异常继续映射到已有安全错误码。

- [ ] **Step 2: 运行红灯测试**

```bash
cd backend
.venv/bin/pytest tests/contract/test_organization_api_adapters.py -q
```

Expected: 因缺少组织检查接口失败。

- [ ] **Step 3: 实现共享树读取边界**

在 `dingtalk.py` 提取部门树和人员归属读取逻辑。`inspect_organization` 只返回部门元数据、部门 ID 归属组合及汇总人数；部门范围的 `capture` 完全跳过 `/topapi/v2/user/list`。

- [ ] **Step 4: 验证并提交**

```bash
cd backend
.venv/bin/pytest tests/contract/test_organization_api_adapters.py -q
.venv/bin/ruff check app/api_connectors/contracts.py app/api_connectors/dingtalk.py
.venv/bin/mypy app/api_connectors/contracts.py app/api_connectors/dingtalk.py
git add app/api_connectors/contracts.py app/api_connectors/dingtalk.py tests/contract/test_organization_api_adapters.py
git commit -m "feat: inspect DingTalk organization hierarchy"
```

Expected: 测试、lint 和类型检查通过。

---

## Task 3: 分类组织分支并冻结继承映射

**Files:**

- Create: `backend/app/api_connectors/organization_unit_classifier.py`
- Create: `backend/app/ai/skills/classify-dingtalk-organization-units/SKILL.md`
- Modify: `backend/app/api_connectors/service.py`
- Modify: `backend/app/api/routes/api_connectors.py`
- Modify: `backend/app/api_connectors/provider_runtime.py`
- Modify: `backend/app/api_connectors/dingtalk.py`
- Test: `backend/tests/unit/api_connectors/test_organization_unit_classifier.py`
- Test: `backend/tests/unit/ai/test_agent_skill_content.py`
- Test: `backend/tests/integration/api/test_api_connectors.py`

**Interfaces:**

```python
class OrganizationClassificationResult(BaseModel):
    department_entity_kinds: dict[str, Literal["teacher", "student"]]
    input_hash: str
    output_hash: str
    skill_version: Literal["1.0.0"]
    attempts: tuple[ClassificationAttemptEvidence, ...]

class DingTalkOrganizationUnitClassifier:
    async def classify(
        self, inspection: OrganizationInspection
    ) -> OrganizationClassificationResult: ...
```

- [ ] **Step 1: 写失败测试**

断言模型请求只包含部门树；输出必须逐一覆盖输入 ID 且只使用 `teacher/student/unknown`；已分类祖先强制所有后代继承，后代反向覆盖、虚构/遗漏/重复 ID、有人分支为 unknown、跨教师与学生分支归属均安全失败；结构错误最多修复两次，总调用不超过三次。

- [ ] **Step 2: 运行红灯测试**

```bash
cd backend
.venv/bin/pytest tests/unit/api_connectors/test_organization_unit_classifier.py tests/unit/ai/test_agent_skill_content.py tests/integration/api/test_api_connectors.py -q
```

Expected: 分类器、Skill 和服务编排测试失败。

- [ ] **Step 3: 实现 Skill、分类器和连接测试编排**

Skill `classify-dingtalk-organization-units@1.0.0` 不使用工具。分类器通过 `build_agent_request` 和 `build_json_repair_request` 调用模型，服务端验证完整 ID 集、父子一致性、未知有人分支和冲突归属。

人员/全部连接测试流程固定为：

```text
读取安全快照 -> 模型分类 -> 服务端继承/校验 -> 保存映射与哈希 -> 适配器测试
```

保存内容为 `department_entity_kinds` 与 `organization_classification`，后者仅含 Skill 版本、树指纹、输入/输出哈希及模型调用摘要，不保存原始模型输出。任务采集校验树指纹并复用映射；历史配置继续走旧逻辑。

- [ ] **Step 4: 验证并提交**

```bash
cd backend
.venv/bin/pytest tests/unit/api_connectors/test_organization_unit_classifier.py tests/unit/ai/test_agent_skill_content.py tests/integration/api/test_api_connectors.py tests/contract/test_organization_api_adapters.py -q
.venv/bin/ruff check app/api_connectors app/api/routes/api_connectors.py
.venv/bin/mypy app/api_connectors app/api/routes/api_connectors.py
git add app/api_connectors app/api/routes/api_connectors.py app/ai/skills/classify-dingtalk-organization-units/SKILL.md tests/unit/api_connectors/test_organization_unit_classifier.py tests/unit/ai/test_agent_skill_content.py tests/integration/api/test_api_connectors.py tests/contract/test_organization_api_adapters.py
git commit -m "feat: classify DingTalk organization branches"
```

Expected: 分类映射持久化但不出现在 API 响应；后续捕获不再调用模型。

---

## Task 4: 更新对话意图、卡片生命周期和错误文案

**Files:**

- Modify: `backend/app/api/routes/api_connectors.py`
- Modify: `backend/app/api/routes/agent.py`
- Modify: `backend/app/agent_runtime/conversation_connectors.py`
- Modify: `backend/app/ai/conversation_agent.py`
- Modify: `backend/app/ai/skills/converse-school-data-sync/SKILL.md`
- Test: `backend/tests/integration/api/test_api_connectors.py`
- Test: `backend/tests/integration/api/test_agent_api.py`
- Test: `backend/tests/unit/ai/test_conversation_agent.py`

- [ ] **Step 1: 写失败测试**

覆盖范围到 `conversation.context.entity_types` 的固定映射；普通对话、非 API 来源和陈旧 `api_provider_id` 不返回卡片；当前 `api_configuration` 或匹配的待处理 API 来源可恢复卡片；切换非 API 来源清除 provider；权限文案只提“部门或人员目录权限/可见范围”。

- [ ] **Step 2: 运行红灯测试**

```bash
cd backend
.venv/bin/pytest tests/integration/api/test_api_connectors.py tests/integration/api/test_agent_api.py tests/unit/ai/test_conversation_agent.py -q
```

Expected: 范围推导和陈旧卡片用例失败。

- [ ] **Step 3: 实现服务端唯一真相**

创建/测试连接时通过 `entity_kinds_for_scope` 写入实体范围并生成“钉钉部门/人员/全部同步”标题。`_api_card_from_context` 仅在以下任一条件成立时返回卡片：

```python
context.get("decision_kind") == "api_configuration" or source_is_api
```

非 API 意图更新移除 `api_provider_id`。将对话 Skill 升级到 `1.7.0`，明确钉钉提供部门和人员目录，教师/学生由本系统按行政单元推导。

- [ ] **Step 4: 验证并提交**

```bash
cd backend
.venv/bin/pytest tests/integration/api/test_api_connectors.py tests/integration/api/test_agent_api.py tests/unit/ai/test_conversation_agent.py tests/unit/ai/test_agent_skill_content.py -q
git add app/api/routes/api_connectors.py app/api/routes/agent.py app/agent_runtime/conversation_connectors.py app/ai/conversation_agent.py app/ai/skills/converse-school-data-sync/SKILL.md tests/integration/api/test_api_connectors.py tests/integration/api/test_agent_api.py tests/unit/ai/test_conversation_agent.py tests/unit/ai/test_agent_skill_content.py
git commit -m "fix: scope DingTalk cards to current intent"
```

Expected: 无关对话不再出现钉钉卡片，相关未完成连接仍可恢复。

---

## Task 5: 更新前端三范围表单

**Files:**

- Modify: `frontend/src/api/agent.ts`
- Modify: `frontend/src/features/task-create/ConversationApiConnectionCard.tsx`
- Modify: `frontend/src/features/task-create/ConversationCreatePage.tsx`
- Modify: `frontend/src/api/agent.test.ts`
- Modify: `frontend/src/features/task-create/ConversationApiConnectionCard.test.tsx`
- Modify: `frontend/src/features/task-create/ConversationCreatePage.test.tsx`

- [ ] **Step 1: 写失败测试**

断言表单只有 `部门/人员/全部` 三项；不再显示人员类型和班级字段；人员/全部提交 `person_classification_mode`，部门不提交；浏览器永不提交分类映射；响应省略卡片或卡片 active 时立即隐藏。

- [ ] **Step 2: 运行红灯测试**

```bash
cd frontend
npm test -- --run src/api/agent.test.ts src/features/task-create/ConversationApiConnectionCard.test.tsx src/features/task-create/ConversationCreatePage.test.tsx
```

Expected: 当前教师/学生表单相关断言失败。

- [ ] **Step 3: 实现类型和组件**

```typescript
public_configuration: {
  sync_scope: "department" | "people" | "all";
  root_department_id: number;
  person_classification_mode?: "organization_unit_llm";
  number_field?: string;
};
```

组件用 `syncScope` 替代 `personEntityKind`，删除 `classNameField`。将权限、未知分支、冲突归属和组织变化安全码映射为可操作中文；页面继续使用：

```typescript
setApiConnection(response.api_connection ?? undefined);
```

- [ ] **Step 4: 验证并提交**

```bash
cd frontend
npm test -- --run src/api/agent.test.ts src/features/task-create/ConversationApiConnectionCard.test.tsx src/features/task-create/ConversationCreatePage.test.tsx
npm run lint
npm run typecheck
npm run build
git add src/api/agent.ts src/features/task-create/ConversationApiConnectionCard.tsx src/features/task-create/ConversationCreatePage.tsx src/api/agent.test.ts src/features/task-create/ConversationApiConnectionCard.test.tsx src/features/task-create/ConversationCreatePage.test.tsx
git commit -m "feat: add DingTalk synchronization scope selector"
```

Expected: 前端测试和构建全部通过。

---

## Task 6: 验证任务冻结、历史兼容和全量质量门禁

**Files:**

- Modify: `backend/app/agent_runtime/task_service.py`（仅在测试暴露单类型假设时）
- Modify: `backend/app/api_connectors/materializer.py`（仅在测试暴露单类型假设时）
- Modify: `backend/tests/integration/agent_runtime/test_api_task_binding.py`
- Modify: `backend/tests/integration/api_connectors/test_api_authority_materializer.py`

- [ ] **Step 1: 写任务链路测试**

断言任务绑定冻结范围、部门映射、Skill 版本、树指纹和哈希；数学组人员继承教师、七年级一班人员继承学生；物化阶段不调用模型；冲突归属不产生部分写入；历史 `person_entity_kind` 绑定仍可重放。

- [ ] **Step 2: 运行并修复兼容差异**

```bash
cd backend
.venv/bin/pytest tests/integration/agent_runtime/test_api_task_binding.py tests/integration/api_connectors/test_api_authority_materializer.py -q
```

Expected: 测试通过；如旧代码假定单一人员类型，只改 `task_service.py` 或 `materializer.py` 中对应范围推导，不向任务阶段注入分类器。

- [ ] **Step 3: 运行完整质量门禁**

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
cd ..
openspec validate --all --strict --no-interactive
git diff --check
```

Expected: 所有命令通过；普通后端套件仅允许缺少专用环境变量时跳过 PostgreSQL 迁移冒烟测试。

- [ ] **Step 4: 运行 PostgreSQL 迁移冒烟测试**

启动 `infra/docker-compose.yml` 后执行：

```bash
cd backend
RECONCILIATION_MIGRATION_TEST_DATABASE_URL=postgresql+asyncpg://reconcile:reconcile@127.0.0.1:5432/reconcile_migration_test \
  .venv/bin/pytest tests/integration/test_migrations.py::test_clean_postgresql_migration_reaches_head -q
```

Expected: 数据库到达 Alembic head，且本改动没有新增迁移。

- [ ] **Step 5: 提交最终兼容调整**

只暂存本任务实际修改的文件；若没有新修改，不创建空提交。提交信息：

```text
test: verify frozen DingTalk classification
```

---

## Completion Checklist

- [ ] 前端只有部门、人员、全部三种范围。
- [ ] 部门范围不调用模型或人员接口。
- [ ] 人员分类只使用部门层级，后代强制继承祖先类别。
- [ ] 未知、冲突、虚构或遗漏分类均安全阻断。
- [ ] 分类映射被持久化、API 隐藏、任务阶段复用。
- [ ] 无关对话不显示钉钉卡片，相关中断配置仍可恢复。
- [ ] 历史连接兼容，全部测试与质量门禁通过。
