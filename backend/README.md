# Backend Development

## Run the application locally

After installing `backend/.venv` and frontend dependencies, run migrations and start
both services from the repository root:

```bash
cd backend
.venv/bin/alembic upgrade head
cd ../frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173`. The API is served at `http://127.0.0.1:8000` and its
interactive documentation is at `http://127.0.0.1:8000/docs`.

## Configure the enterprise model gateway

Create `backend/.env` from `.env.example`. The file is ignored by Git and is the only
local file where real model credentials belong. Set at least:

```dotenv
RECONCILIATION_LLM_URL=https://gateway.example.com/v1/chat/completions
RECONCILIATION_LLM_API_KEY=replace-with-real-secret
RECONCILIATION_LLM_MODEL=enterprise-model-name
RECONCILIATION_TOKENIZATION_SECRET=replace-with-a-long-random-secret
```

`RECONCILIATION_LLM_URL` is the complete OpenAI-compatible Chat Completions endpoint.
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

Run the opt-in smoke test only after supplying a non-production gateway credential:

```bash
cd backend
RUN_REAL_LLM_TEST=1 .venv/bin/pytest tests/smoke/test_real_llm_gateway.py -q
```

Normal automated tests use an in-process HTTP fake and never call a paid model.
