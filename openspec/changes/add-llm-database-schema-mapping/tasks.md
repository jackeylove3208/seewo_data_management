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

## 3. Verification and handoff

- [ ] 3.1 Run `cd backend && .venv/bin/pytest tests/unit/core/test_config.py -q` and confirm the
  focused suite is GREEN.
- [ ] 3.2 Run `cd backend && .venv/bin/ruff check app/connectors/config_file.py
  app/connectors/configured.py app/core/config.py` and resolve all reported errors.
- [ ] 3.3 Run `openspec validate add-llm-database-schema-mapping --strict --no-interactive` and
  confirm the proposal, design, delta spec, and task checklist validate together.
