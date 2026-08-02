# Third-data LLM Source Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `third_data.third_data` available as an LLM-mapped, read-only authoritative MySQL source using the existing `authority_reader` account.

**Architecture:** A committed YAML descriptor defines the connector's bounded table, key, version, mapping mode, and read-only capabilities. The ignored environment file supplies a database-specific DSN under a new secret reference, while MySQL enforces least privilege with a table-level `SELECT` grant.

**Tech Stack:** MySQL 8.4, YAML connector descriptors, Pydantic settings, SQLAlchemy/asyncmy, pytest.

## Global Constraints

- Reuse the existing `authority_reader` MySQL account.
- Grant only `SELECT` on `third_data.third_data`.
- Keep every DSN and password in ignored `backend/.env`; never print or commit them.
- Configure `third-data-mysql` as `authoritative`, `mapping.mode: llm`, and read-only.
- Do not change table data or schema.

---

### Task 1: Declare the read-only LLM source

**Files:**
- Modify: `backend/tests/unit/core/test_database_connector_file.py`
- Modify: `backend/config/database-connectors.yaml`

**Interfaces:**
- Consumes: `load_database_connector_configurations(Path) -> dict[str, DatabaseConnectorConfiguration]`
- Produces: connector ID `third-data-mysql` using credential reference `secret://connectors/third-data-mysql`

- [ ] **Step 1: Write the failing descriptor assertions**

Add assertions that `third-data-mysql` exists and has database `third_data`, table `third_data`, primary key `row_id`, version column `version`, authoritative role, LLM mapping, empty explicit mappings, read/paginated capabilities enabled, and all mutation capabilities disabled. Update the exact connector-set assertion to include `third-data-mysql`.

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/unit/core/test_database_connector_file.py -q`

Expected: FAIL because `third-data-mysql` is absent.

- [ ] **Step 3: Add the minimal YAML descriptor**

```yaml
  third-data-mysql:
    credential_reference: secret://connectors/third-data-mysql
    dialect: mysql
    database_name: third_data
    table_name: third_data
    primary_key: row_id
    version_column: version
    source_role: authoritative
    mapping:
      mode: llm
    capabilities:
      read: true
      paginated: true
```

- [ ] **Step 4: Run the focused test to verify it passes**

Run: `cd backend && .venv/bin/pytest tests/unit/core/test_database_connector_file.py -q`

Expected: `2 passed`.

- [ ] **Step 5: Commit the descriptor and test**

```bash
git add backend/config/database-connectors.yaml backend/tests/unit/core/test_database_connector_file.py
git commit -m "feat: add third-data llm source connector"
```

### Task 2: Provision the local credential and least-privilege grant

**Files:**
- Modify locally, never stage: `backend/.env`

**Interfaces:**
- Consumes: existing `secret://connectors/authority-mysql` DSN and `authority_reader` password
- Produces: `secret://connectors/third-data-mysql` DSN selecting database `third_data`

- [ ] **Step 1: Update the ignored credential map without exposing secrets**

Programmatically parse `RECONCILIATION_DATABASE_CONNECTOR_CREDENTIALS`, clone the existing authority-reader URL with only its database changed to `third_data`, add it under `secret://connectors/third-data-mysql`, and serialize it back to `backend/.env`. Do not print either URL.

- [ ] **Step 2: Grant exact table read access**

Using a local MySQL administrative connection, run:

```sql
GRANT SELECT ON `third_data`.`third_data` TO 'authority_reader'@'localhost';
GRANT SELECT ON `third_data`.`third_data` TO 'authority_reader'@'127.0.0.1';
```

Apply only to account host variants that actually exist; MySQL 8 grants do not create users.

- [ ] **Step 3: Verify the effective grant**

Connect through the new credential and execute `SELECT COUNT(*) FROM third_data`. Report only success and the count, never row contents.

- [ ] **Step 4: Verify writes are denied**

Attempt an `INSERT` using the new credential inside an explicit transaction, expect MySQL access-denied error, and roll back defensively. Use synthetic values only.

### Task 3: Verify the application boundary

**Files:**
- Verify only: `backend/.env`
- Verify only: `backend/config/database-connectors.yaml`

**Interfaces:**
- Consumes: `app.core.config.get_settings()` and `ConfiguredDatabaseConnectorRuntime`
- Produces: a loadable, schema-discoverable and paginated read-only source

- [ ] **Step 1: Validate settings without printing secrets**

Load settings and assert the connector role is `authoritative`, mapping mode is `llm`, read and paginated are true, and create/update/delete are false.

- [ ] **Step 2: Validate runtime schema discovery and paginated read**

Resolve `third-data-mysql`, assert discovered field names are exactly `row_id`, `category`, `name`, `number`, `class_name`, `phone`, `email`, `version`, and `updated_at`, freeze the six canonical mappings, then request one record page. Report only the number of returned records.

- [ ] **Step 3: Run focused quality gates**

Run:

```bash
cd backend
.venv/bin/pytest tests/unit/core/test_database_connector_file.py tests/unit/core/test_config.py -q
.venv/bin/ruff check config tests/unit/core/test_database_connector_file.py
.venv/bin/mypy app
```

Expected: all tests pass, Ruff reports no errors, and mypy reports no issues.

- [ ] **Step 4: Confirm repository hygiene**

Run: `git status --short --ignored backend/.env backend/config/database-connectors.yaml backend/tests/unit/core/test_database_connector_file.py`

Expected: `backend/.env` remains ignored and only intended tracked files were committed.
