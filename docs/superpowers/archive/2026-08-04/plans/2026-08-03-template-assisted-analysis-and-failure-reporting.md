# Template-assisted analysis and failure reporting implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make large CSV reconciliation runs reliable without changing connector-specific behavior: restore 10-item batches, reuse one validated model analysis template for homogeneous `target_extra`/`target_missing` items, and report actionable failure causes.

**Architecture:** Keep normal model analysis for ambiguous and field-level differences. Add a connector-neutral profile and run-scoped checkpoint for safe template reuse, retain existing per-item findings and governance checks, and separate transport retries from semantic model attempts. Build terminal failure reports from persisted safe facts, with a best-effort one-call model explanation and deterministic fallback.

**Tech stack:** FastAPI, SQLAlchemy async, Pydantic, pytest, React/TypeScript.

## Global constraints

- Never persist or log raw personnel values in template keys, checkpoints, or failure telemetry.
- Preserve existing MySQL, HTTPS/API, DingTalk, approval, rollback, and manual-termination behavior.
- No database migration: use existing `agent_checkpoints` and failure records.

## Tasks

- [ ] Restore `analysis_batch_size` default to 10 and update configuration tests.
- [ ] Add strict template input/output contracts and a dedicated analysis-template Skill.
- [ ] Add connector-neutral eligibility/profile logic for unambiguous `target_extra` and `target_missing`; group batches by profile and persist one validated template per run/profile.
- [ ] Instantiate normal per-item findings from the template while preserving each work item’s evidence, operation allowlist, risk policy, and audit trail; fall back to the existing model path whenever eligibility or validation fails.
- [ ] Replace broad structured-output errors with bounded repair codes and preserve completed tool/checkpoint progress between semantic attempts.
- [ ] Add bounded transport retries with safe provider codes and metadata, without consuming extra semantic attempts.
- [ ] Add failure-analysis facts, one best-effort read-only model explanation, and deterministic fallback; distinguish manual termination from system failure followed by termination.
- [ ] Add focused unit/integration tests for homogeneous large CSV runs, mixed/ambiguous fallback, cross-connector isolation, resume behavior, provider errors, and failure reports.
- [ ] Run backend quality gates and relevant frontend tests, then commit the implementation without staging unrelated workspace changes.
