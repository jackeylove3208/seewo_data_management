# LLM Database Schema Mapping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Load readable database connector descriptors from YAML, infer and freeze fixed
organization-field mappings with the existing database-schema Skill, and add
`seewo-data-mysql` with generated-primary-key support.

**Architecture:** Connector configuration describes only a server-selected table, stable key,
version column, role, mapping mode, and capabilities. The graph ingestion path reflects bounded
schema metadata, invokes `understand-organization-database-schema` only on a cache miss, validates
and freezes the result, and passes the frozen mapping into ingestion, execution, verification,
and rollback. SQL stores derive their effective physical access set from that mapping and return
generated primary keys for create operations.

**Tech Stack:** Python 3.12, Pydantic Settings, PyYAML, SQLAlchemy async, FastAPI, pytest,
OpenSpec.

## Global Constraints

- Preserve immutable historical `workflow_version`, `graph_version`, source bindings, and task
  mapping checkpoints.
- Never expose credentials, arbitrary SQL, raw row values before mapping, or generic table access
  to the model.
- Model work uses one initial attempt plus at most three retries.
- The fixed contract remains exactly `category`, `name`, `number`, `class_name`, `phone`, and
  `email`.
- Existing explicit environment JSON connectors remain supported.
- Existing user changes in the worktree must not be modified or committed.

---

### Task 1: Capture the OpenSpec contract and YAML configuration loader

**Files:**
- Create: `openspec/changes/add-llm-database-schema-mapping/proposal.md`
- Create: `openspec/changes/add-llm-database-schema-mapping/design.md`
- Create: `openspec/changes/add-llm-database-schema-mapping/tasks.md`
- Create: `openspec/changes/add-llm-database-schema-mapping/specs/agent-data-ingestion/spec.md`
- Create: `backend/app/connectors/config_file.py`
- Modify: `backend/app/core/config.py`
- Modify: `backend/app/connectors/configured.py`
- Modify: `backend/pyproject.toml`
- Test: `backend/tests/unit/core/test_config.py`

**Interfaces:**
- Produces: `load_database_connector_configurations(path: Path) ->
  dict[str, DatabaseConnectorConfiguration]`
- Produces: `Settings.database_connector_config_file: Path | None`
- Produces: `DatabaseMappingConfiguration(mode: Literal["explicit", "llm"])`
- Preserves: `Settings.database_connector_configurations` as the merged runtime map

- [ ] **Step 1: Write failing configuration tests**

Add tests proving that a YAML file with `mapping.mode: llm` loads a target connector without
`field_columns` or `allowed_columns`, invalid YAML fails closed, duplicate YAML/environment IDs
are rejected, and legacy environment JSON still loads.

```python
def test_settings_load_llm_database_connector_yaml(tmp_path: Path) -> None:
    config = tmp_path / "database-connectors.yaml"
    config.write_text(
        """
connectors:
  seewo-data-mysql:
    credential_reference: secret://connectors/seewo-data-mysql
    dialect: mysql
    database_name: seewo_data
    table_name: data
    primary_key: row_id
    version_column: version
    source_role: target
    mapping:
      mode: llm
    capabilities:
      read: true
      paginated: true
      create: true
      update: true
      delete: true
      optimistic_version: true
      read_after_write: true
""",
        encoding="utf-8",
    )
    settings = Settings(
        database_connector_config_file=config,
        database_connector_credentials={
            "secret://connectors/seewo-data-mysql": "mysql+asyncmy://hidden"
        },
        agent_graph_enabled=True,
        source_ingestion_v3_enabled=True,
        agent_graph_sql_execution_enabled=True,
        new_agent_enabled=True,
        new_agent_analysis_only=False,
        _env_file=None,
    )
    connector = settings.database_connector_configurations["seewo-data-mysql"]
    assert connector.mapping.mode == "llm"
    assert connector.field_columns == {}
    assert connector.allowed_columns == ()
```

- [ ] **Step 2: Run the configuration tests and verify RED**

Run:

```bash
cd backend
.venv/bin/pytest tests/unit/core/test_config.py -q
```

Expected: the YAML setting/model is absent and the new test fails for that reason.

- [ ] **Step 3: Implement strict YAML loading and mapping-mode validation**

Use `yaml.safe_load`, reject non-object roots and unknown keys through Pydantic, resolve relative
paths against `backend/`, reject connector ID collisions, and add `PyYAML>=6,<7` to runtime
dependencies. Explicit mode continues to require a complete target mapping; LLM mode requires no
physical mapping or allow-list.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```bash
cd backend
.venv/bin/pytest tests/unit/core/test_config.py -q
.venv/bin/ruff check app/connectors/config_file.py app/connectors/configured.py app/core/config.py
```

Expected: all selected tests pass and Ruff reports no errors.

- [ ] **Step 5: Create and validate OpenSpec artifacts**

Run:

```bash
openspec validate add-llm-database-schema-mapping --strict --no-interactive
```

Expected: the change validates.

### Task 2: Reflect full bounded schema metadata and derive effective access

**Files:**
- Modify: `backend/app/connectors/configured.py`
- Modify: `backend/app/connectors/database_runtime.py`
- Modify: `backend/tests/contract/test_configured_connectors.py`
- Modify: `backend/tests/fixtures/connector_store.py`

**Interfaces:**
- Produces: `ConnectorColumnSchema` entries with name, SQL type, nullability, primary-key, and
  generated/autoincrement facts
- Produces: `ConfiguredApiConnector.with_frozen_mapping(mapping: Mapping[str, str])`
- Changes: row access requires a frozen mapping for `mapping.mode == "llm"`
- Produces: mutation result containing connector version and optional generated identifier

- [ ] **Step 1: Write failing connector contract tests**

Cover schema discovery without `allowed_columns`, refusal to read rows before freezing, row reads
limited to primary key/version/mapped columns after freezing, and rejection of mappings containing
unknown or duplicate physical columns.

- [ ] **Step 2: Run connector tests and verify RED**

Run:

```bash
cd backend
.venv/bin/pytest tests/contract/test_configured_connectors.py -q
```

Expected: tests fail because schema/access currently depend on configured allow-lists.

- [ ] **Step 3: Implement reflected schema and frozen effective mapping**

Reflect the configured table once. `schema()` returns metadata for all columns, but `page`,
`record`, `mutate`, and `verify` use only the frozen mapping plus primary key/version. Keep
explicit connectors working by treating their configured mapping as pre-frozen.

- [ ] **Step 4: Run connector tests and verify GREEN**

Run:

```bash
cd backend
.venv/bin/pytest tests/contract/test_configured_connectors.py -q
.venv/bin/ruff check app/connectors/configured.py app/connectors/database_runtime.py
```

Expected: all selected tests pass.

### Task 3: Enable cached LLM mapping in source-ingestion-v3

**Files:**
- Modify: `backend/app/agent_graph/production_executor.py`
- Modify: `backend/app/agent_graph/analysis_executors.py`
- Modify: `backend/app/ai/skills/contracts.py`
- Modify: `backend/app/ai/skills/understand-organization-database-schema/SKILL.md`
- Modify: `backend/tests/integration/agent_graph/test_production_runtime.py`
- Modify: `backend/tests/unit/ai/test_agent_skill_content.py`

**Interfaces:**
- Consumes: full `ConnectorSchema` metadata from Task 2
- Produces: one validated `DatabaseSchemaMappingOutput` per connector-pair schema fingerprint
- Persists: each role's canonical-to-physical map in its mapping checkpoint
- Returns: a connector bound to the task's frozen target mapping

- [ ] **Step 1: Write failing graph tests**

Add tests for first-use model invocation, exact mapping validation, cache reuse with zero model
calls, schema-fingerprint invalidation, unresolved mapping failure, and no row read before mapping.

- [ ] **Step 2: Run graph tests and verify RED**

Run:

```bash
cd backend
.venv/bin/pytest \
  tests/integration/agent_graph/test_production_runtime.py \
  tests/unit/ai/test_agent_skill_content.py -q
```

Expected: source-ingestion-v3 currently records an unresolved deterministic checkpoint instead of
invoking the Skill.

- [ ] **Step 3: Implement v3 mapping and checkpoint freezing**

Extract the existing v2 schema-profile/cache logic into focused helpers shared by v2 and v3.
Validate both source roles independently, exclude primary/version references from fixed business
fields, persist mapping and schema fingerprint, and bind the frozen map before normalization.

- [ ] **Step 4: Run graph tests and verify GREEN**

Run:

```bash
cd backend
.venv/bin/pytest \
  tests/integration/agent_graph/test_production_runtime.py \
  tests/unit/ai/test_agent_skill_content.py -q
.venv/bin/ruff check app/agent_graph/production_executor.py app/ai/skills/contracts.py
```

Expected: all selected tests pass.

### Task 4: Make governance execution and rollback consume frozen mappings

**Files:**
- Create: `backend/app/agent_runtime/database_mapping.py`
- Modify: `backend/app/agent_runtime/sql_governance_handlers.py`
- Modify: `backend/app/agent_runtime/sql_rollback_handlers.py`
- Modify: `backend/app/agent_graph/production_executor.py`
- Test: `backend/tests/integration/agent_runtime/test_sql_governance_worker.py`
- Test: `backend/tests/unit/agent_runtime/test_sql_rollback_handlers.py`

**Interfaces:**
- Produces: `load_frozen_database_mapping(session, task_id, run_id, role) ->
  dict[str, str]`
- Consumes: persisted task mapping checkpoint
- Rejects: missing, malformed, stale, or connector-mismatched mapping facts

- [ ] **Step 1: Write failing execution and rollback tests**

Prove that an LLM-mode connector with no static field mapping can execute and roll back using the
checkpoint mapping, while a missing checkpoint fails before mutation.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
cd backend
.venv/bin/pytest \
  tests/integration/agent_runtime/test_sql_governance_worker.py \
  tests/unit/agent_runtime/test_sql_rollback_handlers.py -q
```

Expected: handlers currently read `configuration.field_columns` and fail.

- [ ] **Step 3: Implement shared frozen-mapping loader**

Load and validate task-bound mapping facts once, bind them to the connector used by mutation and
verification, and replace static mapping reads in comparison/restore code.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the same pytest command plus:

```bash
cd backend
.venv/bin/ruff check app/agent_runtime/database_mapping.py \
  app/agent_runtime/sql_governance_handlers.py \
  app/agent_runtime/sql_rollback_handlers.py
```

Expected: tests pass and Ruff is clean.

### Task 5: Support generated MySQL primary keys

**Files:**
- Modify: `backend/app/connectors/configured.py`
- Modify: `backend/app/agent_runtime/sql_governance_handlers.py`
- Modify: `backend/app/agent_runtime/sql_rollback_handlers.py`
- Modify: `backend/tests/contract/test_configured_connectors.py`
- Modify: `backend/tests/integration/agent_runtime/test_sql_governance_worker.py`
- Modify: `backend/tests/unit/agent_runtime/test_sql_rollback_handlers.py`

**Interfaces:**
- Produces: `ConnectorMutationResult(version: ConnectorVersion,
  generated_identifiers: tuple[str | None, ...])`
- Uses: reflected primary-key generated/autoincrement metadata
- Persists: real generated database locator on successful create

- [ ] **Step 1: Write failing generated-key tests**

Create a table with an integer autoincrement primary key. Assert create omits that column, returns
the generated key, verifies by that key, persists `database:<connector>:<key>`, and rollback deletes
the generated row.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
cd backend
.venv/bin/pytest \
  tests/contract/test_configured_connectors.py \
  tests/integration/agent_runtime/test_sql_governance_worker.py \
  tests/unit/agent_runtime/test_sql_rollback_handlers.py -q
```

Expected: current create code explicitly writes the business identifier into the primary key.

- [ ] **Step 3: Implement generated-key mutation results**

For generated keys, omit the physical primary key on insert, capture SQLAlchemy's inserted primary
key, verify using it, and return it to governance. Preserve explicit-key behavior.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the same focused pytest command and relevant Ruff checks.

Expected: all selected tests pass.

### Task 6: Add the Seewo connector configuration and complete verification

**Files:**
- Create: `backend/config/database-connectors.yaml`
- Modify: `backend/.env`
- Modify: `backend/.env.example`
- Modify: `backend/app/agent_runtime/README.md`
- Modify: `openspec/changes/add-llm-database-schema-mapping/tasks.md`

**Interfaces:**
- Adds: `seewo-data-mysql` targeting `seewo_data.data`
- Preserves: existing `authority-mysql` and `seewo-mysql`
- Adds: `secret://connectors/seewo-data-mysql` credential URL ending in `/seewo_data`

- [ ] **Step 1: Add a configuration test for both Seewo targets**

Assert YAML contains both target connectors and the new one uses `row_id`, `version`, and
`mapping.mode: llm`.

- [ ] **Step 2: Run the test and verify RED**

Expected: the YAML file or new connector is absent.

- [ ] **Step 3: Add YAML, credential reference, example, and operator documentation**

Do not expose the credential value in logs or docs. Reuse the existing Seewo host/account/password
while changing only the URL database to `seewo_data`.

- [ ] **Step 4: Verify settings and the live connector**

Run a sanitized settings load, connector health/schema check, and first mapping flow. If the live
LLM is unavailable, run the deterministic test provider integration and report the live dependency
separately.

- [ ] **Step 5: Run repository quality gates**

Run:

```bash
cd backend
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/mypy app
cd ../frontend
npm test -- --run
npm run lint
npm run typecheck
npm run build
cd ..
openspec validate --all --strict --no-interactive
```

Expected: every command exits zero.

- [ ] **Step 6: Mark OpenSpec tasks complete and review the final diff**

Confirm no credentials, unrelated user changes, generated files, or raw MySQL grants are staged.
