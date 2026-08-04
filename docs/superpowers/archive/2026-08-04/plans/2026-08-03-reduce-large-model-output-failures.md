# Reduce Large Model Output Failures Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce large-task structured-output failures by halving the default analysis batch size and sending an explicit 8192-token output limit to the model gateway.

**Architecture:** Keep the existing durable retry and validation flow unchanged. Add one typed model-output setting to `Settings`, use it in the shared OpenAI-compatible request builder, and lower only the default analysis batch size while preserving the existing maximum of ten.

**Tech Stack:** Python 3.12, Pydantic Settings, httpx, pytest, Ruff, mypy.

## Global Constraints

- Default `analysis_batch_size` is `5`; explicitly configured values up to `10` remain valid.
- Default `llm_max_output_tokens` is `8192` and must be a positive integer.
- Every chat-completions request includes `max_tokens`.
- `llm_extra_body_json` remains the final provider-specific override.
- No database migration, adaptive splitting, new telemetry, or UI change.

---

### Task 1: Bound batch and output sizes

**Files:**
- Modify: `backend/app/core/config.py`
- Modify: `backend/app/ai/providers/llm.py`
- Test: `backend/tests/unit/core/test_config.py`
- Test: `backend/tests/unit/ai/test_providers.py`

**Interfaces:**
- Produces: `Settings.llm_max_output_tokens: PositiveInt` with default `8192`.
- Produces: `_request_body(settings, request)` field `max_tokens`.
- Preserves: `Settings.analysis_batch_size` configurable upper bound `10`.

- [ ] **Step 1: Write failing configuration tests**

Add assertions that `Settings(_env_file=None).analysis_batch_size == 5`,
`Settings(_env_file=None).llm_max_output_tokens == 8192`, configured batch size `10` remains valid,
and `llm_max_output_tokens=0` raises Pydantic validation.

- [ ] **Step 2: Write failing provider request tests**

In the existing mock HTTP handlers, decode the request body and assert default requests contain
`"max_tokens": 8192`. Add a request using `llm_max_output_tokens=4096` and assert the body contains
`4096`; add another with `llm_extra_body_json={"max_tokens": 2048}` and assert the provider override
wins.

- [ ] **Step 3: Run focused tests and verify RED**

Run:

```bash
cd backend
.venv/bin/pytest tests/unit/core/test_config.py tests/unit/ai/test_providers.py -q
```

Expected: failures report the old batch default, missing setting, and absent `max_tokens` request field.

- [ ] **Step 4: Implement the minimal settings and request changes**

Change the settings fields to:

```python
llm_max_output_tokens: PositiveInt = 8_192
analysis_batch_size: PositiveInt = Field(default=5, le=10)
```

Build the shared request body with the standard value before the existing provider overrides:

```python
body: dict[str, Any] = {
    "model": settings.llm_model,
    "messages": messages,
    "temperature": request.temperature,
    "max_tokens": settings.llm_max_output_tokens,
    **settings.llm_extra_body_json,
}
```

- [ ] **Step 5: Run focused tests and verify GREEN**

Run:

```bash
cd backend
.venv/bin/pytest tests/unit/core/test_config.py tests/unit/ai/test_providers.py -q
```

Expected: all focused tests pass.

- [ ] **Step 6: Run backend quality gates**

Run:

```bash
cd backend
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/mypy app
```

Expected: all commands exit zero; only environment-gated tests are skipped.

- [ ] **Step 7: Commit the implementation**

```bash
git add backend/app/core/config.py backend/app/ai/providers/llm.py backend/tests/unit/core/test_config.py backend/tests/unit/ai/test_providers.py
git commit -m "fix: reduce large model output failures"
```
