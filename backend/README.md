# Backend Development

## Run the application locally

After installing `backend/.venv` and frontend dependencies, the repository launcher
runs migrations, the API, the durable analysis worker, and Vite together:

```bash
cd frontend
npm run dev
```

For a production-like PostgreSQL setup, run the backend processes separately:

```bash
cd backend
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --reload
```

```bash
cd backend
.venv/bin/python -m app.ai.worker
```

Open `http://127.0.0.1:5173`. The API is served at `http://127.0.0.1:8000` and its
interactive documentation is at `http://127.0.0.1:8000/docs`.

The worker claims one persisted difference at a time, commits progress after each
item, and resumes work whose lease expires. The API process does not execute model
calls inside `workflow/advance`.

## Configure the enterprise model gateway

Put real model URLs, credentials, and model names in `backend/.env`. The file is
ignored by Git and is the only local file where real model credentials belong. Set
at least:

```dotenv
RECONCILIATION_LLM_URL=https://gateway.example.com/v1/chat/completions
RECONCILIATION_LLM_API_KEY=replace-with-real-secret
RECONCILIATION_LLM_MODEL=enterprise-model-name
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
