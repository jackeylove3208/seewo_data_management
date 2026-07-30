## Context

Database connectors are currently configured through an environment JSON mapping whose target
definitions assume that physical field columns and an allowed-column list are known before a task
runs. The approved feature adds a file-based, typed configuration source and an LLM mapping mode
while retaining the existing merged runtime map and legacy environment configuration.

The loader is a settings concern, but its validation rules directly constrain connector inspection
and the ingestion contract. Configuration must therefore fail closed before a worker can claim that
a target is usable.

## Goals / Non-Goals

**Goals:**

- Load connector definitions from strict YAML using the existing Pydantic configuration models.
- Expose `database_connector_config_file`, `DatabaseMappingConfiguration.mode`, and the merged
  `database_connector_configurations` map.
- Support `explicit` and `llm` modes with mode-specific validation.
- Define the LLM mapping output as exactly six fixed fields: `category`, `name`, `number`,
  `class_name`, `phone`, and `email`; reject invented or extra keys.
- Keep model access bounded to schema metadata before mapping and exclude raw rows, credentials,
  arbitrary SQL, generic table access, and unbounded evidence.
- Resume historical tasks from persisted workflow/graph versions, frozen source bindings, and
  mapping checkpoint keys/results; current configuration applies only to new tasks.
- Resolve relative file paths against `backend/`, reject malformed roots, unknown keys, duplicate
  IDs, and unresolved credential references safely.
- Preserve legacy environment JSON loading and explicit-mode behavior.

**Non-Goals:**

- Implementing LLM schema discovery, prompting, mapping inference, or database introspection.
- Changing connector execution, SQL governance, task APIs, migrations, or frontend behavior.
- Supporting arbitrary YAML tags, includes, environment interpolation, or credential material in the
  YAML file.

## Decisions

### Use a dedicated YAML loader behind settings

Add a focused connector configuration loader that accepts a `Path`, parses with `yaml.safe_load`,
and returns typed `DatabaseConnectorConfiguration` values. Settings owns path resolution and merges
the file result with the legacy environment map, keeping connector consumers independent of the
configuration source.

Alternative considered: parse YAML directly in each connector consumer. Rejected because it would
duplicate validation and make the runtime map inconsistent across consumers.

### Make mapping mode explicit and strict

Represent mapping configuration with a literal mode of `explicit` or `llm`. Explicit target
connectors retain the complete physical mapping and allow-list requirements. LLM targets may omit
both `field_columns` and `allowed_columns`; their absence normalizes to `{}` and `()` so downstream
code has a stable shape.

Alternative considered: infer LLM mode from missing fields. Rejected because omission would hide
typos and make an incomplete explicit configuration appear valid.

### Reject collisions instead of applying precedence

When a connector ID exists in both YAML and the environment JSON map, settings raises a validation
error. This prevents an operator from believing one definition is active while the other silently
overrides it. Legacy-only and YAML-only definitions continue to work.

Alternative considered: YAML wins over environment values. Rejected because silent precedence is
unsafe for credentials, mapping policy, and auditability.

### Fail closed on YAML structure and path errors

Only object roots and object-shaped connector entries are accepted; unknown keys are rejected by
Pydantic. Relative paths are resolved from `backend/`, and malformed YAML or invalid configuration
is surfaced as a settings error before runtime use.

Alternative considered: permissive parsing with ignored keys. Rejected because configuration typos
could change the connector contract without operator visibility.

### Keep model-mediated mapping bounded and fixed-contract

Before a mapping decision, the model receives only a bounded, sanitized schema metadata envelope
with an explicit size limit and no row values. The mapping result is validated against the exact
six-field contract (`category`, `name`, `number`, `class_name`, `phone`, `email`); unknown or
invented output keys are rejected. The model boundary has no credential, arbitrary SQL, generic
table-access, or unbounded-evidence capability.

Alternative considered: provide sample rows or a generic database tool so the model can infer
semantics more freely. Rejected because those inputs increase privacy and exfiltration risk and
make the mapping contract non-deterministic.

### Freeze historical execution inputs

At task/run creation, persist the workflow version, graph version, frozen source bindings, and
mapping checkpoint keys/results. Resume logic uses those persisted values and does not reread the
current connector configuration; configuration changes are selected only while creating new tasks.

Alternative considered: re-resolve configuration on every resume. Rejected because a configuration
edit could change the meaning of a historical run, invalidate checkpoint keys, or break replay.

## Risks / Trade-offs

- [Risk] Adding PyYAML increases the runtime dependency surface. → Pin the supported major range
  (`PyYAML>=6,<7`) and exercise parsing through focused settings tests.
- [Risk] Existing deployments may have duplicate IDs while migrating to YAML. → Reject collisions
  deterministically and document that operators must remove one definition before startup.
- [Risk] Relative paths can be interpreted differently by local commands and deployed workers. →
  Resolve them against the repository's `backend/` base and expose the resolved settings path.
- [Risk] LLM mode without a physical allow-list broadens what later mapping code may inspect. → Keep
  mode validation in configuration and defer actual schema access to the separately governed
  ingestion workflow.

## Migration Plan

1. Add the dependency and loader/model changes while keeping the environment JSON setting intact.
2. Deploy with existing environment configuration only and verify legacy tests remain green.
3. Introduce YAML definitions one connector at a time; remove any duplicate environment definition
   before enabling the file in a deployment.
4. Roll back by disabling the YAML setting and reverting the dependency/code change; environment JSON
   remains the supported fallback.

## Open Questions

- The implementation task should confirm the existing settings error type and preserve its public
  error formatting when wrapping YAML/parser failures.
- The later LLM mapping implementation must define the bounded schema evidence and approval policy;
  this configuration change only selects the mapping mode.
- The exact persistence columns and storage model for the frozen task/run contract should follow
  the existing workflow-version and mapping-checkpoint implementation during application work.
