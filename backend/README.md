# Backend Development

## Run the application locally

After installing `backend/.venv`, frontend dependencies, Docker Desktop, and configuring
the ignored `backend/.env`, the root launcher starts PostgreSQL, runs migrations, and
supervises FastAPI, the durable new Agent worker, and Vite:

```bash
cd /path/to/PythonProject
python3 dev.py
```

Open `http://127.0.0.1:5173`. API documentation is available at
`http://127.0.0.1:8000/docs`. The launcher enables the CSV Agent rollout only in its
child processes and never prints or copies `.env` secrets. Run `python3 dev.py --dry-run`
to inspect the non-sensitive startup plan, or `python3 dev.py --no-browser` to skip opening
the browser. Press `Ctrl+C` to stop API, Agent worker, and Vite; PostgreSQL remains running
so demo data is preserved.

The `.env` filename begins with a dot and may be hidden in Finder or an IDE. If it does not
exist, create it from `backend/.env.example` and enter the local DeepSeek and tokenization
settings. Git intentionally ignores this file, so linked worktrees do not receive it.

To run the backend processes separately:

```bash
cd backend
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --reload
```

```bash
cd backend
.venv/bin/python -m app.agent_runtime
```

## Verify delivery readiness

From the repository root, start the local pgvector PostgreSQL service before running
the PostgreSQL migration smoke test:

```bash
docker compose -f infra/docker-compose.yml up -d
cd backend
RECONCILIATION_MIGRATION_TEST_DATABASE_URL=postgresql+asyncpg://reconcile:reconcile@127.0.0.1:5432/reconcile_migration_test \
  .venv/bin/pytest tests/integration/test_migrations.py::test_clean_postgresql_migration_reaches_head -q
```

The smoke test drops, creates, migrates, and removes only the dedicated
`reconcile_migration_test` database. It refuses the ordinary `reconcile` database
and skips with an explicit message if its environment variable is absent.

Run backend checks from `backend/`:

```bash
.venv/bin/python -m pip install --constraint requirements-ci.txt -e '.[dev]'
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/mypy app
```

Run frontend checks from `frontend/`:

```bash
npm ci
npm test -- --run
npm run lint
npm run typecheck
npm run build
```

These commands match GitHub Actions. They use test fixtures only and do not need a
model gateway credential; the separate opt-in model smoke test remains outside CI.

## Configure the enterprise model gateway

Put real model URLs, credentials, and model names in `backend/.env`. The file is
ignored by Git and is the only local file where real model credentials belong. Set
at least:

```dotenv
RECONCILIATION_LLM_URL=https://gateway.example.com/v1/chat/completions
RECONCILIATION_LLM_API_KEY=replace-with-real-secret
RECONCILIATION_LLM_MODEL=enterprise-model-name
RECONCILIATION_CONVERSATION_CONTEXT_MAX_TOKENS=65536
RECONCILIATION_CONVERSATION_CONTEXT_RESERVED_OUTPUT_TOKENS=2048
RECONCILIATION_TOKENIZATION_SECRET=replace-with-a-long-random-secret
RECONCILIATION_PROPOSAL_PREVIEW_SECRET=replace-with-another-long-random-secret
RECONCILIATION_EMBEDDING_URL=https://gateway.example.com/v1/embeddings
RECONCILIATION_EMBEDDING_API_KEY=replace-with-real-secret
RECONCILIATION_EMBEDDING_MODEL=enterprise-embedding-name
RECONCILIATION_EMBEDDING_DIMENSIONS=1536
RECONCILIATION_EMBEDDING_TIMEOUT_SECONDS=20
```

`RECONCILIATION_LLM_URL` is the complete OpenAI-compatible Chat Completions endpoint.
The proposal preview secret must be identical across API instances so a signed batch
preview remains valid after a restart or load-balanced request.
Keep `RECONCILIATION_LLM_RESPONSE_MODE=json_schema` when the gateway supports strict
structured output; use `json_object` or `prompt_json` for compatible gateways that do
not support JSON Schema.

The conversation Agent sends the complete persisted chat history on every turn. It does
not summarize or silently truncate older messages. Configure the model's total context
window with `RECONCILIATION_CONVERSATION_CONTEXT_MAX_TOKENS` and reserve enough capacity
for the structured reply with `RECONCILIATION_CONVERSATION_CONTEXT_RESERVED_OUTPUT_TOKENS`.
When the complete request no longer fits, the API stops before calling the provider and
asks the operator to open a new conversation.

Provider-specific keyword arguments belong in validated JSON rather than Python code:

```dotenv
RECONCILIATION_LLM_EXTRA_HEADERS_JSON={"X-Gateway-App":"reconciliation"}
RECONCILIATION_LLM_EXTRA_BODY_JSON={"top_p":0.8,"max_tokens":2000}
```

Extra values cannot override `model`, `messages`, `response_format`, `stream`, the
authentication header, or `Content-Type`. For an API-key header without `Bearer`, use:

```dotenv
RECONCILIATION_LLM_AUTH_HEADER=X-API-Key
RECONCILIATION_LLM_AUTH_SCHEME=
```

## Configure Agent API and database connectors

Organization API ingestion is disabled by default. The backend registers audited DingTalk and
WeCom manifests with fixed provider endpoints; the model cannot discover endpoints or construct
arbitrary HTTP requests. Generate one Fernet key and keep it only in the ignored `backend/.env`:

```bash
cd backend
.venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Configure one server-owned writable MySQL target. The configuration contains allow-listed table and
column metadata plus an opaque credential reference; the matching DSN stays in the secret map.

```dotenv
RECONCILIATION_NEW_AGENT_ENABLED=true
RECONCILIATION_AGENT_GRAPH_ENABLED=true
RECONCILIATION_SOURCE_INGESTION_V3_ENABLED=true
RECONCILIATION_AGENT_GRAPH_SQL_EXECUTION_ENABLED=true
RECONCILIATION_NEW_AGENT_ANALYSIS_ONLY=false
RECONCILIATION_NEW_AGENT_API_CONNECTOR_ENABLED=true
RECONCILIATION_API_CONNECTOR_SECRET_KEY=replace-with-generated-fernet-key
RECONCILIATION_DATABASE_CONNECTOR_CONFIGURATIONS={"seewo-mysql":{"credential_reference":"secret://connectors/seewo-mysql","dialect":"mysql","table_name":"organization_people","primary_key":"id","version_column":"row_version","field_columns":{"category":"category","name":"name","number":"number","class_name":"class_name","phone":"phone","email":"email"},"source_role":"target","capabilities":{"read":true,"paginated":true,"create":true,"update":true,"delete":true,"optimistic_version":true,"read_after_write":true}}}
RECONCILIATION_DATABASE_CONNECTOR_CREDENTIALS={"secret://connectors/seewo-mysql":"mysql+asyncmy://user:password@host/database"}
```

Run `alembic upgrade head` before enabling the flags, then restart both FastAPI and
`python -m app.agent_runtime`. The secure conversation card creates a one-time configuration
session, submits DingTalk `app_key`/`app_secret` or WeCom `corp_id`/`corp_secret` directly to the
connector API, and tests permissions and visibility. Chat, model payloads, task intent, Graph state,
events, reports, and logs receive only the connection ID and sanitized status.

The safe connector control plane is:

- `GET /api/connectors/providers`
- `POST /api/connectors/configuration-sessions`
- `POST/GET /api/connectors/connections`
- `POST /api/connectors/connections/{id}/test`
- `POST /api/connectors/connections/{id}/rotate-secret`
- `DELETE /api/connectors/connections/{id}`

Provider access is always read-only. A confirmed task freezes
`agent-sync-graph-v2`, `source-ingestion-v3`, and `deterministic-execution-v2`, captures complete
paginated API evidence, then reuses the existing identity, AI analysis, governance, MySQL
preflight, idempotent mutation, write-verification, reporting, and rollback pipeline.

Run only synthetic connector tests during development:

```bash
cd backend
.venv/bin/pytest tests/contract/test_organization_api_adapters.py \
  tests/integration/api/test_api_connectors.py \
  tests/integration/api_connectors/test_materializer.py \
  tests/integration/agent_runtime/test_api_task_binding.py \
  tests/e2e/test_agent_graph_lifecycle.py -q
```

These fixtures never use production teacher, student, department, credential, or token data.
Rotating a connection secret affects future captures only; an in-progress run continues from its
immutable captured evidence. To roll back new task creation, set
`RECONCILIATION_NEW_AGENT_API_CONNECTOR_ENABLED=false` and restart API and worker. Keep
`source-ingestion-v3` and Graph v2 code deployed while existing API runs finish, and retain the
additive connection, source, and identity-binding rows for audit rather than downgrading them.

Embedding access has the same gateway controls, but uses independent settings so
the chat and embedding deployments can differ:

```dotenv
RECONCILIATION_EMBEDDING_AUTH_HEADER=X-API-Key
RECONCILIATION_EMBEDDING_AUTH_SCHEME=
RECONCILIATION_EMBEDDING_EXTRA_HEADERS_JSON={"X-Gateway-App":"entity-rematching"}
RECONCILIATION_EMBEDDING_EXTRA_BODY_JSON={"encoding_format":"float"}
```

The configured embedding dimensions must match the provider response and the
PostgreSQL vector schema (currently 1536). Rematching and quality policy defaults
can also be overridden in `backend/.env`:

```dotenv
RECONCILIATION_REMATCHING_ENABLED=false
RECONCILIATION_REMATCHING_SHADOW_MODE=true
RECONCILIATION_REMATCHING_TOP_K=3
RECONCILIATION_REMATCHING_HIGH_CONFIDENCE_THRESHOLD=0.9
RECONCILIATION_REMATCHING_WORKER_LEASE_SECONDS=60
RECONCILIATION_REMATCHING_WORKER_CONCURRENCY=4
RECONCILIATION_REMATCHING_WORKER_RETRY_ATTEMPTS=3
RECONCILIATION_REMATCHING_WORKER_RETRY_WAIT_SECONDS=2
RECONCILIATION_MATCHING_QUALITY_POLICY_VERSION=matching-quality-v1
RECONCILIATION_MATCHING_QUALITY_MIN_POPULATION=10
RECONCILIATION_MATCHING_QUALITY_MAX_UNRESOLVED_RATIO=0.2
RECONCILIATION_MATCHING_QUALITY_MAX_CREATE_RATIO=0.2
RECONCILIATION_MATCHING_QUALITY_MAX_DISABLE_RATIO=0.2
```

Run the opt-in smoke test only after supplying a non-production gateway credential:

```bash
cd backend
RUN_REAL_LLM_TEST=1 .venv/bin/pytest tests/smoke/test_real_llm_gateway.py -q
```

Normal automated tests use an in-process HTTP fake and never call a paid model.
