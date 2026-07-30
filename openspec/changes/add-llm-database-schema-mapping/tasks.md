## 1. Configuration tests

- [ ] 1.1 Add focused settings tests for a YAML target using `mapping.mode: llm` without
  `field_columns` or `allowed_columns`, asserting normalized empty mapping values.
- [ ] 1.2 Add tests proving malformed YAML and non-object roots fail closed, duplicate YAML/environment
  connector IDs are rejected, and legacy environment JSON still loads.
- [ ] 1.3 Run `cd backend && .venv/bin/pytest tests/unit/core/test_config.py -q` and confirm the new
  behavior is initially RED before implementation.

## 2. YAML configuration model and loader

- [ ] 2.1 Add the typed `DatabaseMappingConfiguration` model with literal `explicit` and `llm` modes
  and mode-specific validation.
- [ ] 2.2 Implement `load_database_connector_configurations(path: Path) ->
  dict[str, DatabaseConnectorConfiguration]` using `yaml.safe_load`, strict Pydantic validation,
  non-object-root rejection, and safe credential-reference handling.
- [ ] 2.3 Add `Settings.database_connector_config_file: Path | None`, resolve relative paths against
  `backend/`, merge YAML with the legacy environment JSON map, and reject duplicate connector IDs
  instead of applying precedence.
- [ ] 2.4 Preserve `Settings.database_connector_configurations` as the single merged runtime map and
  keep explicit-mode target requirements unchanged while allowing LLM mode to omit physical mapping
  and allow-list fields.
- [ ] 2.5 Add the constrained runtime dependency `PyYAML>=6,<7`.

## 3. Fixed mapping and model safety contract

- [ ] 3.1 Define and validate the exact mapping fields `category`, `name`, `number`, `class_name`,
  `phone`, and `email`; reject invented or extra keys for deterministic and model-produced mappings.
- [ ] 3.2 Implement the model boundary so pre-mapping input is a bounded, sanitized schema-metadata
  envelope only, with no raw rows, credentials, arbitrary SQL, generic table access, or unbounded
  evidence; add denial/error handling for prohibited requests.
- [ ] 3.3 Add focused contract tests for prohibited model inputs, bounded evidence, and extra mapping
  keys, including the exact six-field output contract.
- [ ] 3.4 Persist and consume `workflow_version`, `graph_version`, frozen source bindings, mapping
  checkpoint keys, and mapping checkpoint results on resume; ensure current configuration affects
  only newly created tasks.
- [ ] 3.5 Add tests proving a historical task resumes from its persisted contract after configuration
  changes and a new task freezes the current configuration independently.

## 4. Verification and handoff

- [ ] 4.1 Run `cd backend && .venv/bin/pytest tests/unit/core/test_config.py -q` and confirm the
  focused suite is GREEN.
- [ ] 4.2 Run `cd backend && .venv/bin/ruff check app/connectors/config_file.py
  app/connectors/configured.py app/core/config.py` and resolve all reported errors.
- [ ] 4.3 Run `openspec validate add-llm-database-schema-mapping --strict --no-interactive` and
  confirm the proposal, design, delta spec, and task checklist validate together.
