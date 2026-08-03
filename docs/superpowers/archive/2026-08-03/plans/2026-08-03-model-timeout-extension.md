# Model timeout extension implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Increase the default model request timeout to 120 seconds while keeping the Agent worker lease safely longer.

**Architecture:** Keep the existing settings interface and environment overrides. Change only the two coordinated defaults and their configuration contract test.

**Tech Stack:** Python 3.12, Pydantic Settings, pytest.

## Global constraints

- Model batches remain limited to 10 records.
- Each model subtask remains limited to four logical attempts.
- The worker lease must remain strictly longer than the model request timeout.

---

### Task 1: Extend the timeout and worker lease

**Files:**
- Modify: `backend/app/core/config.py`
- Test: `backend/tests/unit/core/test_config.py`

**Interfaces:**
- Consumes: `Settings.llm_timeout_seconds` and `Settings.analysis_worker_lease_seconds`.
- Produces: defaults of 120 seconds and 150 seconds respectively.

- [ ] **Step 1: Update the configuration contract test**

```python
def test_agent_model_timeout_allows_structured_analysis_to_finish() -> None:
    assert Settings(_env_file=None).llm_timeout_seconds == 120
    assert Settings(_env_file=None).analysis_worker_lease_seconds == 150
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `.venv/bin/pytest tests/unit/core/test_config.py::test_agent_model_timeout_allows_structured_analysis_to_finish -q`

Expected: failure showing the old defaults `60` and `90`.

- [ ] **Step 3: Change the coordinated defaults**

```python
llm_timeout_seconds: PositiveFloat = 120
analysis_worker_lease_seconds: PositiveInt = 150
```

- [ ] **Step 4: Run focused and regression checks**

Run: `.venv/bin/pytest tests/unit/core/test_config.py tests/unit/ai/test_providers.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/config.py backend/tests/unit/core/test_config.py
git commit -m "fix: extend model request timeout"
```
