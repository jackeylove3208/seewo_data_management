# Remote CSV persistence and deletion repair implementation plan

> **For Codex:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Make conversation-triggered remote CSV tasks persist successfully on PostgreSQL and make failed or completed remote-source tasks deletable without leaving managed files behind.

**Architecture:** Preserve the existing full UUID-plus-SHA-256 remote storage filename and widen the database contract to fit it. Extend the task deletion transaction so task-bound remote-source rows are removed before their restricted parent rows, then remove only the exact managed files associated with those remote-source identifiers after commit.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL, pytest.

## Global constraints

- Work only in the isolated branch and preserve all dirty files in the main worktree.
- Keep the remote filename format `<remote_source_uuid_hex>-<sha256>.csv`.
- `source_files.storage_name` must support the complete 101-character filename; use `VARCHAR(128)`.
- Delete task-bound `remote_sources` rows before deleting referenced `source_files` or `reconciliation_tasks`.
- Remove remote artifacts only after the database transaction commits successfully.
- File cleanup must be narrowly scoped to exact remote-source UUID prefixes under the configured managed remote upload directory.
- Cover production changes with tests that fail before implementation and pass afterward.
- Do not delete the real failed task with raw SQL; exercise the application deletion API after migration.

### Task 1: Widen the remote storage filename contract

**Files:**
- Modify: `backend/app/models/snapshots.py`
- Create: `backend/alembic/versions/0038_expand_source_file_storage_name.py`
- Test: `backend/tests/unit/models/test_source_file_model.py` or the nearest existing source-file model contract test
- Test: `backend/tests/integration/test_migrations.py`

- [ ] Add a regression test asserting the SQLAlchemy model exposes `source_files.storage_name` as length 128 and accepts the full remote filename shape.
- [ ] Run the focused test and confirm it fails against the current length-80 contract.
- [ ] Change the model column to `String(128)`.
- [ ] Add an Alembic migration that upgrades `source_files.storage_name` from `VARCHAR(80)` to `VARCHAR(128)` and provides the inverse downgrade.
- [ ] Extend migration verification to assert the PostgreSQL column length is 128 at head.
- [ ] Run focused model and migration tests and confirm they pass.
- [ ] Commit the task with a Conventional Commit message.

### Task 2: Delete remote-source tasks safely

**Files:**
- Modify: `backend/app/tasks/deletion_service.py`
- Modify: task deletion route wiring under `backend/app/api/`
- Test: `backend/tests/integration/tasks/test_task_deletion.py`

- [ ] Add regression coverage for a failed task that has a `RemoteSourceRecord` but no `SourceFile`; deletion must remove the remote-source row, task, and its exact completed/partial managed artifacts.
- [ ] Add regression coverage for a materialized remote task with a referenced `SourceFile`; deletion must respect foreign-key ordering and remove managed files after commit.
- [ ] Run the focused deletion tests and confirm the new cases fail before implementation.
- [ ] Inject the configured remote upload root into `TaskDeletionService`.
- [ ] Query task-bound remote-source identifiers, delete their rows before source files and task rows, and preserve the existing audit/deletion behavior.
- [ ] After a successful commit, unlink only files matching each exact remote-source UUID prefix inside the configured remote upload root; make missing files harmless.
- [ ] Update all service construction sites.
- [ ] Run focused deletion and API tests and confirm they pass.
- [ ] Commit the task with a Conventional Commit message.

### Task 3: Verify production contracts and recover the demo

**Files:**
- Modify only if a verification failure exposes a scoped defect.

- [ ] Run focused remote-source materialization and task-deletion suites.
- [ ] Run the full backend pytest suite, Ruff, and mypy.
- [ ] Run the clean PostgreSQL migration smoke test against `reconcile_migration_test`.
- [ ] Review the complete branch diff for scope, migration safety, cleanup boundaries, and accidental generated files.
- [ ] Apply Alembic head to the local development database.
- [ ] Restart the local API and worker with remote CSV conversation ingestion enabled.
- [ ] Delete failed task `0331361c-8064-41eb-9220-f2681096a5cd` through the application API and verify its task, remote-source row, and managed artifact are gone.
- [ ] Re-run the public CSV conversation flow and verify materialization passes the previous failure point.
- [ ] Keep the temporary public CSV URL online for the user until handoff.
- [ ] Commit any final scoped verification fixes, obtain whole-branch review, and merge the branch into `master` without disturbing the main worktree's existing changes.
