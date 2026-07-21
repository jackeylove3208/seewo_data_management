# AI model configuration hardening implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make local AI analysis configuration independent of the launch directory, compatible with the verified DeepSeek endpoint, and safe from committed or traceback-exposed credentials.

**Architecture:** `Settings` will use an absolute default path derived from the backend package while preserving environment-variable precedence. The HTTP provider will keep credentials wrapped as `SecretStr` until authorization-header construction. Checked-in examples will contain placeholders; the ignored local file will hold the corrected endpoint, response mode, and generated preview secret.

**Tech Stack:** Python 3.12, Pydantic Settings, httpx, pytest, pytest-asyncio, dotenv files, Git file permissions.

## Global Constraints

- Real credentials remain only in ignored `backend/.env`.
- `RECONCILIATION_LLM_URL` is the complete endpoint `https://api.deepseek.com/chat/completions`.
- The configured model remains `deepseek-v4-flash`.
- The configured response mode is `json_object` because the verified gateway rejects the current JSON Schema request.
- API and worker processes must resolve the same settings from repository root or `backend/`.
- Raw API-key strings must not be passed as exception-visible provider function arguments.
- The committed example must not contain a key-shaped usable credential.
- Do not modify unrelated dirty files or rewrite Git history.

---

### Task 1: Make settings and examples deterministic and safe

**Files:**
- Modify: `backend/app/core/config.py`
- Modify: `backend/.env.example`
- Test: `backend/tests/unit/core/test_config.py`
- Test: `backend/tests/unit/security/test_env_example.py`

**Interfaces:**
- Produces `DEFAULT_ENV_FILE: Path` and `Settings.model_config["env_file"] == DEFAULT_ENV_FILE`.
- Keeps explicit process environment variables higher priority than the default file.

- [ ] **Step 1: Write failing tests**

```python
from pathlib import Path

from app.core.config import DEFAULT_ENV_FILE, Settings


def test_default_env_file_is_backend_absolute_path() -> None:
    assert DEFAULT_ENV_FILE == Path(__file__).resolve().parents[3] / ".env"
    assert DEFAULT_ENV_FILE.is_absolute()
    assert Settings.model_config["env_file"] == DEFAULT_ENV_FILE


def test_example_contains_no_usable_api_key() -> None:
    example = (Path(__file__).resolve().parents[3] / ".env.example").read_text()
    assert "replace-with-real-secret" in example
    assert "sk-" not in example
```

- [ ] **Step 2: Run the tests and observe the expected RED**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/core/test_config.py tests/unit/security/test_env_example.py -q`

Expected: FAIL because the settings path constant and the example placeholder are not implemented.

- [ ] **Step 3: Implement the absolute env path and sanitize the example**

```python
# backend/app/core/config.py
from pathlib import Path

DEFAULT_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=DEFAULT_ENV_FILE,
        env_prefix="RECONCILIATION_",
        extra="ignore",
    )
```

Set the example values to `RECONCILIATION_LLM_URL=https://api.deepseek.com/chat/completions`, `RECONCILIATION_LLM_MODEL=deepseek-v4-flash`, `RECONCILIATION_LLM_RESPONSE_MODE=json_object`, and `RECONCILIATION_LLM_API_KEY=replace-with-real-secret`. Do not put a generated local secret in the example.

- [ ] **Step 4: Run the focused tests and existing config tests**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/core/test_config.py tests/unit/security/test_env_example.py tests/unit/ai/test_providers.py -q`

Expected: all focused tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/config.py backend/.env.example backend/tests/unit/core/test_config.py backend/tests/unit/security/test_env_example.py
git commit -m "fix: load AI settings from backend env"
```

### Task 2: Prevent provider traceback credential exposure

**Files:**
- Modify: `backend/app/ai/providers/llm.py`
- Test: `backend/tests/unit/ai/test_providers.py`

**Interfaces:**
- `HttpLLMProvider.complete_json()` continues accepting `LLMRequest` and returning `LLMResponse`.
- `_complete_with()` receives `SecretStr` rather than a raw API-key string; `_request_headers()` is the only function that extracts the value for the outbound header.

- [ ] **Step 1: Write the failing traceback-redaction test**

```python
import httpx


async def test_llm_failure_traceback_does_not_contain_api_key() -> None:
    secret = "test-api-key-that-must-not-leak"
    settings = Settings(
        _env_file=None,
        llm_url="https://model.example.test/chat/completions",
        llm_api_key=secret,
        tokenization_secret="tokenization-secret",
    )

    async def fail(*_args, **_kwargs):
        raise httpx.ConnectError("connection failed")

    provider = HttpLLMProvider(settings=settings, client=httpx.AsyncClient(transport=httpx.MockTransport(fail)))
    with pytest.raises(TransientModelError) as captured:
        await provider.complete_json(LLMRequest(messages=(Message(role="user", content="ping"),)))
    traceback = captured.value.__traceback__
    while traceback is not None:
        if traceback.tb_frame.f_code.co_filename.endswith("app/ai/providers/llm.py"):
            assert secret not in repr(traceback.tb_frame.f_locals)
        traceback = traceback.tb_next
    await provider.client.aclose()
```

- [ ] **Step 2: Run the test and observe RED**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/ai/test_providers.py::test_llm_failure_traceback_does_not_contain_api_key -q`

Expected: FAIL because the raw key is currently a local traceback-visible function argument.

- [ ] **Step 3: Keep the key masked until header construction**

Pass `SecretStr` through `complete_json()` and `_complete_with()`. Change `_request_headers(settings, api_key)` to accept `SecretStr`, extract with `get_secret_value()` only when building the authorization value, and keep all retry/error paths free of raw-key locals.

- [ ] **Step 4: Run provider tests**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/ai/test_providers.py -q && .venv/bin/ruff check app/ai/providers/llm.py`

Expected: all provider tests PASS and Ruff reports no errors.

- [ ] **Step 5: Commit**

```bash
git add backend/app/ai/providers/llm.py backend/tests/unit/ai/test_providers.py
git commit -m "fix: redact LLM credentials from errors"
```

### Task 3: Correct ignored local configuration and verify end to end

**Files:**
- Modify ignored local file: `backend/.env`
- No committed source files

**Interfaces:**
- `Settings()` resolves the same configured endpoint/model/response mode from repository root and `backend/`.
- The real smoke test exercises the configured endpoint without printing credentials.

- [ ] **Step 1: Update only the ignored local values**

Set:

```dotenv
RECONCILIATION_LLM_URL=https://api.deepseek.com/chat/completions
RECONCILIATION_LLM_MODEL=deepseek-v4-flash
RECONCILIATION_LLM_RESPONSE_MODE=json_object
RECONCILIATION_PROPOSAL_PREVIEW_SECRET=<new random 64-hex-character secret>
```

Keep the existing local API key and tokenization secret unchanged. Do not print any value.

- [ ] **Step 2: Restrict local file permissions**

Run: `chmod 600 backend/.env`

Expected: `stat -f '%Sp' backend/.env` prints `-rw-------`.

- [ ] **Step 3: Verify settings from both working directories without values**

Run both:

```bash
cd backend && .venv/bin/python -c 'from app.core.config import Settings; s=Settings(); print(s.llm_url, s.llm_model, s.llm_response_mode.value, s.model_gateway_configured)'
cd .. && PYTHONPATH=backend backend/.venv/bin/python -c 'from app.core.config import Settings; s=Settings(); print(s.llm_url, s.llm_model, s.llm_response_mode.value, s.model_gateway_configured)'
```

Expected: both print the same endpoint, `deepseek-v4-flash`, `json_object`, and `True`; never print keys.

- [ ] **Step 4: Run final verification**

Run: `cd backend && .venv/bin/python -m pytest tests/unit/core/test_config.py tests/unit/security/test_env_example.py tests/unit/ai/test_providers.py -q && RUN_REAL_LLM_TEST=1 .venv/bin/python -m pytest tests/smoke/test_real_llm_gateway.py -q`

Expected: focused tests pass and the real smoke test reports `1 passed`.

- [ ] **Step 5: Review status and commit only tracked code**

Run: `git status --short --branch && git diff --check HEAD~2..HEAD`

Expected: unrelated pre-existing changes remain untouched; `backend/.env` remains ignored and unstaged.
