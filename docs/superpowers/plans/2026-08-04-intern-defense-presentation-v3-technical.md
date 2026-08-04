# 实习课题答辩演示 v3 技术深潜版 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** 将现有 HTML 答辩演示重构为约 17 页、15 分钟可讲完的开发者向数据治理技术演示。

**Architecture:** 保留现有单文件 HTML 的翻页与响应式结构，重写页面内容和少量 CSS 组件。新增两张用户提供的 Skill 截图，并用真实 Agent-Graph/Supervisor/Skill 契约字段、虚构数据记录、三道门伪代码和三代工作流问题链构成技术叙事。使用 Playwright 在 1365×768 与 1920×1080 下逐页检查溢出、截图完整性和文字重叠。

**Tech Stack:** HTML5, CSS3, vanilla JavaScript, local PNG assets, Playwright Chromium.

## Global Constraints

- 演示约 17 页，适配约 15 分钟，每页只承担一个结论。
- 前三页必须明确数据治理包含数据同步，不能将同步写成项目终点。
- Agent-Graph、Supervisor、Skill、Sub-agent 使用仓库真实字段；伪代码必须标明为示例。
- 数据流只使用虚构记录，不展示真实个人数据或手机号。
- 当前边界固定写为“CSV、MySQL、API、HTTPS 已打通；后续是连接器扩展以及量化评估”。
- 保留现有浅灰背景、蓝/绿/橙强调、卡片和案例截图风格。
- 1365×768 与 1920×1080 下不得出现文字溢出、重叠、截图裁切或导航失效。

---

### Task 1: 准备截图资源与现有 HTML 基线

**Files:**
- Create: `assets/cases/skill-normalize.png`
- Create: `assets/cases/skill-reconcile.png`
- Read/Modify: `AI组织架构数据治理系统-实习课题答辩-v2.html`

**Interfaces:**
- Consumes: 用户提供的两张 PNG 临时文件、现有 17 页 HTML。
- Produces: 可在浏览器直接加载的本地 Skill 图片路径，以及可定位的现有页面结构。

- [ ] **Step 1: 复制用户提供的 Skill 截图**

运行：

```bash
cp /var/folders/ql/zvf9hmm16g935286bfv2pv0m0000gn/T/codex-clipboard-ddc60d3f-7dba-49be-89a0-819d587e4512.png assets/cases/skill-normalize.png
cp /var/folders/ql/zvf9hmm16g935286bfv2pv0m0000gn/T/codex-clipboard-cd6ded9b-39c5-48b8-9be1-4bbe80f827b1.png assets/cases/skill-reconcile.png
```

预期：两个文件存在且尺寸非零。

- [ ] **Step 2: 建立资源清单**

运行：

```bash
file assets/cases/skill-normalize.png assets/cases/skill-reconcile.png
```

预期：两张图片均被识别为 PNG；记录其尺寸用于页面布局。

- [ ] **Step 3: 记录现有页面索引**

运行：

```bash
rg -n 'data-slide|<section class="slide|<h1|<h2' AI组织架构数据治理系统-实习课题答辩-v2.html
```

预期：确认现有 17 个 `data-slide`，后续按同一导航脚本保持 17 页。

### Task 2: 重写前半段治理叙事与总体架构

**Files:**
- Modify: `AI组织架构数据治理系统-实习课题答辩-v2.html`，页面 0–5 和对应 CSS 组件

**Interfaces:**
- Consumes: `基于 AI 的魔方组织架构&三方数据分析与治理系统.md`、现有案例截图、设计稿页面 1–5。
- Produces: 封面、深层问题、治理闭环、入口案例、总体架构和技术取舍六页。

- [ ] **Step 1: 修改封面与问题页**

将封面主标题改为“基于 AI 的组织数据治理系统”；第二页标题改为“为什么必须做数据治理”，四个问题块分别写多源维护、身份对应、字段/结构差异、写入风险，并在页脚明确“数据同步只是接入层”。

- [ ] **Step 2: 新增治理闭环页**

用原生 HTML 卡片绘制：`接入 → 冻结快照 → 规范化 → 对账分析 → finding → 治理计划 → 执行验证 → 报告/回滚`，标出第三方只读和希沃目标写入，加入当前边界文字。

- [ ] **Step 3: 保留并修正数据同步助手案例页**

使用 `assets/cases/sync-assistant.png`，标题固定为“数据同步助手演示”，把说明改为“连接与范围确认是治理入口”，保证标题、图片和设计取舍不重叠。

- [ ] **Step 4: 重写总体架构与技术取舍页**

画出接入、证据、Agent-Graph、Skill/Sub-agent、治理执行、报告审计六层；添加纯规则、单 Agent、自由多 Agent、受控 Agent-Graph 的对比，并明确当前方案的代价与收益。

### Task 3: 重写 Agent-Graph、Supervisor、Skill/Sub-agent 技术页

**Files:**
- Modify: `AI组织架构数据治理系统-实习课题答辩-v2.html`，页面 6–10 和对应 CSS

**Interfaces:**
- Consumes: `backend/app/agent_graph/definition.py`, `contracts.py`, `actions.py`, `runtime.py`, `analysis_executors.py`, `backend/app/ai/graph_subagents.py`, 两张 Skill PNG。
- Produces: 开发者可读的节点图、Supervisor JSON、启动顺序图、两张 Skill 实现截图。

- [ ] **Step 1: 画关键 Agent-Graph 节点**

绘制真实节点名称和节点类型标签：`inspect_sources`、`normalize_input_batches`、`validate_input_contract`、`build_identity_index`、`analyze_actionable_batches`、`aggregate_risk`、`preflight_execution`、`execute_ready_operations`、`verify_operations`、`generate_terminal_report`。使用不同颜色区分 decision、deterministic、human gate、sub-agent、report。

- [ ] **Step 2: 加入 Supervisor 输入/输出代码卡**

展示 `current_node`、`action_set.allowed_actions`、`evidence_manifest_refs`、`pending_work_summary` 输入，以及 `action_id`、`expected_result`、`risk_notes_zh`、`why_not_other_actions_zh` 输出。示例动作必须是 `analyze_batch_8a12` 与 `resolve_identity_conflicts` 二选一。

- [ ] **Step 3: 画 node → Supervisor → Skill → sub-agent 顺序图**

顺序图中显示 `graph_cursor`、`action_id`、`skill_name@version`、`evidence_manifest_id`、`input_hash`、schema 校验、checkpoint 持久化和最多重试，不使用大段中文解释替代字段。

- [ ] **Step 4: 放置两张 Skill 图片**

页面 9 使用 `skill-normalize.png`，页面 10 使用 `skill-reconcile.png`；每页旁边只列 phase、allowed_tools、input/output schema、证据边界、是否允许写入，图片必须保持可读。

### Task 4: 重写数据流、Finding/M​​utation/Report、三道门

**Files:**
- Modify: `AI组织架构数据治理系统-实习课题答辩-v2.html`，页面 11–13 和对应 CSS

**Interfaces:**
- Consumes: `contracts.py`, 三个执行 Skill 文档、设计稿页面 11–13。
- Produces: 一条虚构记录的端到端流转、可恢复对象关系图、带代码的三道门。

- [ ] **Step 1: 绘制虚构记录流转**

使用 `STU-001 / 林小满 / 高一(1)班`，依次展示 raw record、快照引用、六字段规范化、identity work item、`target_missing` finding、create mutation、verification ref、报告事实。

- [ ] **Step 2: 解释 Finding、Mutation、Report 的关系**

用三张对象卡和箭头说明聊天文本不承担恢复状态；恢复依赖 `snapshot_id`、`evidence_manifest_id`、finding、冻结 mutation、`verification_ref` 和报告事实。加上版本变化时安全失败的条件。

- [ ] **Step 3: 添加三道门代码**

分别展示输入门检查快照/合同/批次覆盖，分析门检查身份候选/finding/冲突，执行门检查锁/版本/审批/幂等/读后验证；高风险冲突指向人工门，明确不能跳过或直接生成 SQL/API。

### Task 5: 重写三代工作流与总结页

**Files:**
- Modify: `AI组织架构数据治理系统-实习课题答辩-v2.html`，页面 14–16 和对应 CSS

**Interfaces:**
- Consumes: `docs/superpowers/reference/backend/2026-08-04-agent-sync-bugfix-history.md`、Git 提交记录、设计稿页面 14–17。
- Produces: 三代架构总览、问题/根因/修正表、agent-graph-v1 重点设计、项目思考总结。

- [ ] **Step 1: 制作三代工作流时间线**

突出 `legacy-v1 → new-agent-v1 → agent-graph-v1`，每代写清流程形态、状态边界、主要问题和升级原因。

- [ ] **Step 2: 制作故障驱动演进页**

覆盖钉钉分类、大批次 JSON、工具检查点丢失、失败报告覆盖、模板与复杂项隔离五类问题，每项呈现旧现象、根因、架构修正和 Git 证据。

- [ ] **Step 3: 制作第三代架构重点页**

用突出色块写 action set、冻结快照复用、Skill 版本钉死、证据清单、人工 gate、幂等执行、读后验证、独立回滚和复杂项隔离。

- [ ] **Step 4: 重写最后一页**

最后一页只做项目思考总结，保留五条原则和当前边界，不再堆叠新的技术细节。

### Task 6: 视觉 QA 与交付

**Files:**
- Modify: `AI组织架构数据治理系统-实习课题答辩-v2.html`
- Create: `.local/ppt-v3-qa/`

**Interfaces:**
- Consumes: 完整 17 页 HTML 与本地 PNG 资源。
- Produces: 在两种视口下通过检查的 HTML 演示。

- [ ] **Step 1: 启动 Playwright 并检查页数/图片**

在 1365×768 和 1920×1080 加载本地文件，断言 `document.querySelectorAll('.slide').length === 17`，并等待 Skill/case 图片 `complete && naturalWidth > 0`。

- [ ] **Step 2: 检查溢出与重叠**

逐页计算 `.slide` 内元素边界，记录超出 viewport、标题与正文相交、图片被裁切、代码卡超出容器的项目；优先用 CSS 缩放和网格列宽修复。

- [ ] **Step 3: 检查交互**

验证右箭头、左箭头、Space、Home、End 和鼠标左右区域翻页，确认页码仍为 `n / 17`。

- [ ] **Step 4: 生成视觉检查截图**

保存每页 PNG 和一张 montage，使用 `view_image` 检查第 2、6、7、8、9、10、11、13、15、16 页的文字密度与图片可读性。

- [ ] **Step 5: 交付前验证**

运行：

```bash
test -s AI组织架构数据治理系统-实习课题答辩-v2.html
git status --short
```

预期：HTML 非空；不修改用户已有 CSV 变更；只报告本次 HTML、Skill 资源和必要设计文件。
