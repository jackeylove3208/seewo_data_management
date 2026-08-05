# 基于 AI 的组织架构三方数据分析与治理系统

这是一个面向组织架构数据治理的全栈演示项目：通过 AI 辅助分析权威数据与目标系统数据之间的差异，生成可审阅、可追踪的治理结果，并支持后续同步与审计。

项目由以下部分组成：

- `backend/`：FastAPI 接口、SQLAlchemy 数据模型、Alembic 数据库迁移、AI 分析服务和持久化 Agent Worker。
- `frontend/`：基于 React、TypeScript、Vite 和 Ant Design 的数据治理工作台。
- `infra/`：本地 PostgreSQL（包含 pgvector）服务配置。
- `docs/sample-data/`：仅用于演示的虚构样例数据。

## 主要能力

- 导入组织架构权威数据和第三方目标数据。
- 对人员、组织、岗位等信息进行匹配和差异识别。
- 使用 AI 分析差异原因并给出治理建议。
- 记录任务、阶段、执行结果和审计信息，支持失败恢复和人工确认。
- 通过网页工作台或后端 OpenAPI 接口查看治理任务。

## 运行环境

建议使用以下版本：

- Python 3.12。
- Node.js 22 和 npm。
- Docker Desktop，并确保 Docker Compose 可用。
- 如果要运行 AI Agent，需要一个兼容当前 Chat Completions 调用方式的模型服务和有效 API Key。单元测试使用虚拟模型，不需要真实模型凭证。

## 安装依赖

在项目根目录执行：

```bash
cd backend
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
cd ../frontend
npm ci
cd ..
```

如果本机没有 `python3.12`，请先安装 Python 3.12，再创建虚拟环境。后续后端命令均使用 `backend/.venv/bin/` 下的程序。

## 配置 `.env`

后端配置文件是 `backend/.env`，不要把真实配置直接写入代码或 README。首次使用时复制模板：

```bash
cp backend/.env.example backend/.env
```

至少需要修改模型服务相关配置。下面是一个最小示例，实际值请替换为自己的配置：

```dotenv
RECONCILIATION_DATABASE_URL=postgresql+asyncpg://reconcile:reconcile@localhost:5432/reconcile
RECONCILIATION_LLM_URL=https://your-model-gateway.example.com/v1/chat/completions
RECONCILIATION_LLM_API_KEY=replace-with-your-real-api-key
RECONCILIATION_LLM_MODEL=your-model-name
RECONCILIATION_TOKENIZATION_SECRET=replace-with-a-long-random-secret
RECONCILIATION_PROPOSAL_PREVIEW_SECRET=replace-with-another-long-random-secret
```

配置项说明：

- `RECONCILIATION_DATABASE_URL`：本地 Docker PostgreSQL 使用示例中的连接地址即可。
- `RECONCILIATION_LLM_URL`：模型服务的 Chat Completions 地址；项目示例默认使用 DeepSeek 兼容地址，也可以替换为其他兼容服务。
- `RECONCILIATION_LLM_API_KEY`：模型服务 API Key，属于敏感信息，只能保存在本机的 `.env` 中。
- `RECONCILIATION_LLM_MODEL`：模型服务实际支持的模型名称。
- `RECONCILIATION_TOKENIZATION_SECRET`：用于敏感字段处理和相关签名的随机密钥，建议使用长度足够的随机字符串，至少 16 个字符。
- `RECONCILIATION_PROPOSAL_PREVIEW_SECRET`：治理建议预览签名密钥，建议单独生成；留空时应用会回退使用 tokenization secret。

可以使用下面的命令生成随机密钥，再复制输出结果到 `.env`：

```bash
openssl rand -hex 32
```

`backend/.env.example` 还包含上传目录、超时、重试、Embedding、Agent 开关、API 连接器和数据库连接器等完整配置。没有使用外部连接器时，保持连接器开关为 `false`，相关配置保持为空对象即可。

如果使用数据库连接器，连接描述可以放在 `backend/config/database-connectors.yaml`，账号、密码和其他凭证只放在 `backend/.env` 的凭证配置中。不要把真实数据库密码写入 YAML、代码、日志或截图。

前端通常不需要单独配置，因为 Vite 会把 `/api` 和 `/health` 请求代理到 `http://127.0.0.1:8000`。如果前端需要访问其他 API 地址，可以创建 `frontend/.env`：

```dotenv
VITE_API_BASE_URL=http://127.0.0.1:8000
```

## 一键启动完整演示

确保 Docker Desktop 已启动，并且 `backend/.env`、`backend/.venv`、`frontend/node_modules` 都已准备好，然后在项目根目录执行：

```bash
python3 dev.py
```

启动脚本会依次完成以下工作：

1. 启动 `infra/docker-compose.yml` 中的 PostgreSQL。
2. 执行 Alembic 数据库迁移。
3. 启动 FastAPI 后端。
4. 启动持久化 Agent Worker。
5. 启动 Vite 前端开发服务器。
6. 自动打开浏览器。

启动成功后：

- 前端地址：`http://127.0.0.1:5173`
- API 文档：`http://127.0.0.1:8000/docs`
- 就绪检查：`http://127.0.0.1:8000/health/ready`

脚本会为本地完整演示开启受控 Agent Graph 和 CSV 执行配置。按 `Ctrl+C` 会停止本地启动的 API、Worker 和前端进程；如需停止 PostgreSQL，再执行：

```bash
docker compose -f infra/docker-compose.yml down
```

只想检查启动命令而不真正启动服务：

```bash
python3 dev.py --dry-run
```

不自动打开浏览器：

```bash
python3 dev.py --no-browser
```

## 手动启动

如果需要分别查看每个服务的日志，可以使用四个终端窗口。

终端一：启动 PostgreSQL。

```bash
docker compose -f infra/docker-compose.yml up -d --wait
```

终端二：执行迁移并启动 API。

```bash
cd backend
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

终端三：启动 Agent Worker。

```bash
cd backend
.venv/bin/python -m app.agent_runtime
```

终端四：启动前端。

```bash
cd frontend
npm run dev:web
```

## 基本使用流程

1. 打开 `http://127.0.0.1:5173`。
2. 按页面引导选择或上传权威组织架构数据和第三方目标数据。
3. 创建治理任务，等待匹配、差异分析和 AI 建议生成。
4. 在工作台中查看差异项、分析依据、治理建议和任务事件。
5. 对需要变更的数据进行人工确认，再执行同步或导出。
6. 通过任务详情和审计记录追踪执行结果；如果 Worker 中途退出，重新启动 Worker 后会根据租约和检查点继续处理。

项目提供的演示数据均为虚构数据，可以从以下文件开始体验：

- 权威数据：`docs/sample-data/data/agent_generated_100_third_party_authoritative.csv`
- 目标数据：`docs/sample-data/seewo/agent_generated_100_seewo_target_with_10_errors.csv`
- MySQL 连接器演示种子：`docs/sample-data/mysql/reconciliation-demo-seed.sql`

## 开发和验证命令

后端检查：

```bash
cd backend
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/mypy app
```

前端检查：

```bash
cd frontend
npm test -- --run
npm run lint
npm run typecheck
npm run build
npm run test:e2e
```

OpenSpec 文档检查：

```bash
cd ..
openspec validate --all --strict --no-interactive
```

PostgreSQL 迁移冒烟测试需要先启动 Docker，并使用专用测试数据库：

```bash
cd backend
RECONCILIATION_MIGRATION_TEST_DATABASE_URL=postgresql+asyncpg://reconcile:reconcile@127.0.0.1:5432/reconcile_migration_test \
  .venv/bin/pytest tests/integration/test_migrations.py::test_clean_postgresql_migration_reaches_head -q
```

## 目录和配置约定

- 上传文件、快照、隔离文件和导出文件默认位于 `backend/storage/`。
- 数据库迁移位于 `backend/alembic/`。
- 后端测试位于 `backend/tests/`，前端测试位于 `frontend/src/` 和 `frontend/tests/`。
- API 的交互式文档由 FastAPI 自动生成，访问 `http://127.0.0.1:8000/docs` 即可。
- 当前 `.gitignore` 只保留 `.env` 规则，因此 `.venv`、`node_modules`、缓存、SQLite 数据库和生成文件也可能被 Git 识别为待上传内容；提交前请根据仓库体积和发布需要检查 `git status` 与暂存区。

## 上传前的安全检查

数据样例是虚构的，但模型 API Key、数据库密码、连接器凭证、签名密钥和本地路径仍然属于配置或凭证信息。上传前请确认：

- 真实凭证只存在于 `backend/.env` 或 `frontend/.env`，没有复制到其他文件。
- README、示例配置、日志和截图中使用的是占位符。
- 没有使用 `git add -f` 强制添加 `.env`。
- 使用 `git diff --cached` 检查暂存内容，确认没有误把本机密钥或日志加入提交。

示例配置文件可以提交；其中的 `replace-with-...` 只是占位符，不能替代真实运行配置。
