# Fix v2 database fingerprint implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow governance execution to reopen the frozen v2 MySQL mapping created by the production task service.

**Architecture:** Keep the persisted 64-character `SourceFile.sha256` contract unchanged. Make the v2 compatibility validator accept both the production raw digest and the historical prefixed fact-hash representation while continuing to compare the complete frozen connector configuration.

**Tech Stack:** Python 3.12, FastAPI domain services, SQLAlchemy, pytest.

## Global constraints

- Do not weaken connector ID, role, storage-path, mapping, or schema-fingerprint checks.
- Do not write to the configured external MySQL databases during the regression test.

---

### Task 1: Reproduce and fix the v2 fingerprint mismatch

**Files:**
- Modify: `backend/tests/integration/agent_runtime/test_sql_governance_worker.py`
- Modify: `backend/app/agent_runtime/database_mapping.py`

**Interfaces:**
- Consumes: production task-service `SourceFile.sha256` values containing a 64-character raw SHA-256 digest.
- Produces: `_v2_database_configuration_fingerprints(...) -> frozenset[str]` containing accepted legacy and current representations.

- [x] **Step 1: Write the failing test**

Add a test that builds the existing v2 mapping fixture, replaces its stored source fingerprint with the raw complete-configuration digest used by `AgentTaskService`, and resolves the frozen target connector.

- [x] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/integration/agent_runtime/test_sql_governance_worker.py::test_v2_frozen_mapping_accepts_task_service_configuration_fingerprint -q`

Expected: fail with `database connector configuration changed after task creation`.

- [x] **Step 3: Implement the minimal compatibility fix**

Return both raw 64-character digests and prefixed `sha256:` forms from `_v2_database_configuration_fingerprints`, preserving both the legacy reduced configuration and current complete configuration payloads.

- [x] **Step 4: Run the focused and surrounding tests**

Run the new regression test, the v2 mapping drift tests, and the SQL governance integration module; expect all to pass.

- [x] **Step 5: Run static checks**

Run Ruff on the modified backend files and MyPy on `app`; expect exit code 0.
