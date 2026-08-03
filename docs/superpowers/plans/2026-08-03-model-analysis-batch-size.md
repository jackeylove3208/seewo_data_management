# Model Analysis Batch Size Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Limit shared CSV and database reconciliation model calls to 10 work items while retaining four total attempts.

**Architecture:** Make the maximum an invariant of the shared batch planner, validate the same bound in settings, and pass the configured value from both worker entry points. Do not change the model retry loop.

**Tech Stack:** Python 3.12, SQLAlchemy, Pydantic Settings, pytest

## Global Constraints

- A reconciliation analysis model batch contains 1 through 10 work items.
- Model execution remains one initial attempt plus three retries.
- CSV and database tasks use the same behavior.

---

### Task 1: Enforce and propagate the batch limit

**Files:**
- Modify: `backend/app/ai/agent_batching.py`
- Modify: `backend/app/agent_graph/production_executor.py`
- Modify: `backend/app/agent_runtime/csv_analysis_worker.py`
- Modify: `backend/app/core/config.py`
- Test: `backend/tests/unit/ai/test_agent_batching.py`
- Test: `backend/tests/integration/agent_runtime/test_agent_identity_handler.py`
- Test: `backend/tests/unit/core/test_config.py`

**Interfaces:**
- `AgentBatchPlanner(session, max_items=10)` controls persisted analysis batch membership.
- `Settings.analysis_batch_size` accepts only values from 1 through 10.

- [ ] Add failing tests for 43-item partitioning, configured planner partitioning, and configuration bounds.
- [ ] Run the focused tests and confirm they fail for the missing limit propagation.
- [ ] Implement the shared 10-item invariant and pass settings from both execution entry points.
- [ ] Run focused and related tests, then lint and type-check the backend.
- [ ] Commit the verified change.
