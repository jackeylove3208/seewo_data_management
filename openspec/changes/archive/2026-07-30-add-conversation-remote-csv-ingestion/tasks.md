## 1. Conversation contract and persistence

- [x] 1.1 Add remote-source settings, SQLAlchemy model, repository, and Alembic migration with tenant/operator/conversation/task/file bindings and safe lifecycle fields
- [x] 1.2 Add deterministic one-link extraction, HTTPS registration validation, origin cleaning, and raw-link replacement tests and implementation
- [x] 1.3 Extend conversation context, decision, and intent contracts with trusted remote-source references and reject unlisted or cross-conversation selections
- [x] 1.4 Integrate registration and sanitized history into the conversation message endpoint without changing no-link behavior
- [x] 1.5 Reject `remote_csv` from the manual task endpoint and require a matching conversation binding in `AgentTaskService`

## 2. Safe remote materialization

- [x] 2.1 Add unit-tested URL, public-address, redirect, downgrade, timeout, content-length, streamed-size, and content-type policies
- [x] 2.2 Implement a connection-pinned HTTPS downloader with injectable DNS/network dependencies and managed temporary-file cleanup
- [x] 2.3 Materialize valid CSV content into an immutable authoritative `SourceFile` and `Snapshot`, persisting hash and safe provenance idempotently
- [x] 2.4 Add safe failure classification and ensure no partial file, task resource, raw URL, query string, or response body is exposed

## 3. Versioned graph integration

- [x] 3.1 Add `agent-sync-graph-v2` with `materialize_sources` while preserving sync v1 and rollback graph definitions
- [x] 3.2 Route only conversation remote-source tasks to graph v2 and record the initial materialization transition after school-lock acquisition
- [x] 3.3 Add candidate selection and deterministic `materialize_remote_authority` execution with checkpoint/idempotency evidence
- [x] 3.4 Add graph worker recovery tests for success, safe failure, interrupted publication, and unchanged historical graph behavior

## 4. MCP and remote source-understanding Skill

- [x] 4.1 Add bounded materialized-source profile/page resources to the CSV mapping evidence manifest and retain forbidden URL argument checks
- [x] 4.2 Add and validate `understand-remote-organization-source@1.0.0` with fixed CSV mapping contracts and allowed read-only MCP tools
- [x] 4.3 Route ambiguous remote CSV mappings to the new Skill, keep known headers deterministic, and validate all returned references and normalizers
- [x] 4.4 Add Skill, tool-authorization, prompt-injection, tokenization, and invalid-output tests

## 5. API and UI behavior

- [x] 5.1 Add conversation API tests for one link, no link, multiple/invalid links, sanitization, source confirmation, and task creation
- [x] 5.2 Add manual API regression tests proving forged URL and `remote_csv` requests fail before task/run/lock creation
- [x] 5.3 Update conversation API types and presentation to display only the cleaned remote origin; leave manual-sync UI and API types unchanged
- [x] 5.4 Add frontend unit and Playwright coverage for link-triggered conversation confirmation and absence of manual link controls

## 6. Verification and documentation

- [x] 6.1 Run focused backend and frontend tests after each TDD cycle and update this checklist with completed work
- [x] 6.2 Run backend pytest, Ruff, mypy, frontend tests, lint, typecheck, build, migration smoke test, and strict OpenSpec validation
- [x] 6.3 Update connector/runtime documentation with the conversation-only remote CSV contract, safe limits, operator errors, and deployment flag
