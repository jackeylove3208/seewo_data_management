## Why

Third-party authoritative organization data may be published as a public CSV download, making a
local download-and-upload step impractical and prone to stale inputs. The conversation Agent needs
a safe way to turn a link sent in chat into an immutable task resource without exposing arbitrary
URLs to models or expanding the manual-sync surface.

## What Changes

- Detect at most one public HTTPS CSV link in a user conversation message, register it to the
  authenticated tenant, operator, and conversation, and replace the raw URL with a safe origin
  summary before any model call or persisted chat display.
- Add a conversation-only `remote_csv` authoritative source selection. Reject this source kind from
  the manual task endpoint before task creation or school-lock acquisition.
- Add a versioned sync graph node and deterministic action that validates the destination network,
  follows bounded safe redirects, downloads within existing size/time limits, inspects CSV format,
  and materializes one immutable `SourceFile` snapshot.
- Add bounded MCP evidence for the materialized resource and a versioned Skill for ambiguous
  organization-field semantics. Models never receive a URL or perform the download.
- Preserve `agent-sync-graph-v1` recovery semantics and all existing manual CSV, local, and SQL
  task behavior.

## Capabilities

### New Capabilities

- `conversation-remote-csv-source`: Conversation-only public HTTPS CSV registration, safe
  materialization, immutable provenance, retry behavior, and user-visible failure semantics.

### Modified Capabilities

- `conversational-task-creation`: Recognize a link sent in chat as a server-registered
  authoritative source while keeping the raw URL outside model context and persisted chat display.
- `agent-data-ingestion`: Accept a materialized remote CSV snapshot as authoritative input and
  conditionally resolve ambiguous fixed-six-field mappings.
- `agent-skill-mcp-security`: Authorize model access only to task-bound materialized evidence and
  continue rejecting arbitrary URL arguments.
- `multi-agent-reconciliation-runtime`: Add a versioned materialization node before source
  inspection for remote-source tasks while preserving historical graph versions.
- `external-data-sync`: Explicitly keep the manual-sync entry upload/configuration-only and reject
  `remote_csv`.

## Impact

The change affects conversation request handling and contracts, Agent task intent validation,
SQLAlchemy models and Alembic migrations, graph definitions/candidate selection/action execution,
CSV storage and network policy code, MCP evidence schemas, one new runtime Skill, API and graph
tests, and conversation UI presentation tests. It uses the existing `httpx`, CSV inspection,
`SourceFile`, snapshot, checkpoint, evidence-manifest, tokenization, and worker infrastructure; no
new external service or browser automation dependency is introduced.
