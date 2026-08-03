# Included Quality Warning Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep missing student `class_name` visible as a quality anomaly while preventing included records from being described as excluded or blocked, including in existing reports.

**Architecture:** Add one backend fact-to-narrative canonicalizer for future persisted reports and one frontend fact-to-view canonicalizer for immutable historical reports. Both use `excluded_findings[].inclusion_state` as the authority and leave actual excluded/anomalous marks unchanged.

**Tech Stack:** Python 3.12, FastAPI/Pydantic, pytest, React/TypeScript, Vitest/Testing Library.

## Global Constraints

- Do not change ingestion eligibility or rewrite historical reports.
- `authority_field_unavailable` with `inclusion_state=included` remains counted as a quality anomaly.
- Included records must be described as remaining in matching and synchronization scope.
- Existing excluded/anomalous reporting remains unchanged.

---

### Task 1: Canonicalize future backend report narratives

**Files:**
- Modify: `backend/app/agent_graph/report_executors.py`
- Test: `backend/tests/integration/agent_graph/test_reporting.py`

**Interfaces:**
- Consumes: frozen `facts["excluded_findings"]` and model `InputExceptionAnalysis` output.
- Produces: `_included_quality_warning_analyses(facts: Mapping[str, Any]) -> dict[str, dict[str, str]]` and persisted canonical narrative items.

- [ ] **Step 1: Write the failing integration assertion**

Extend the report fixture so `authority_field_unavailable` is an exclusive reason and the provider claims the included students were excluded. Assert persisted content instead contains “允许同步”, “仍保留在匹配与同步范围内”, and does not contain the provider's exclusion statement.

- [ ] **Step 2: Run the test to verify RED**

Run: `/Users/lbs/PycharmProjects/PythonProject/backend/.venv/bin/pytest tests/integration/agent_graph/test_reporting.py::test_graph_report_uses_model_narrative_but_server_facts -q`

Expected: FAIL because persisted `impact_zh` still says the included records were excluded.

- [ ] **Step 3: Implement the fact-derived replacement**

Add `_included_quality_warning_analyses` to aggregate included `authority_field_unavailable` marks, localize student/class fields, and return the canonical five narrative fields. Before `AgentReportingService.generate`, replace only matching model analyses; retain all other model analyses unchanged.

- [ ] **Step 4: Run backend tests to verify GREEN**

Run: `/Users/lbs/PycharmProjects/PythonProject/backend/.venv/bin/pytest tests/integration/agent_graph/test_reporting.py tests/unit/agent_runtime/test_csv_governance_handlers.py -q`

Expected: PASS.

### Task 2: Correct immutable historical report presentation

**Files:**
- Modify: `frontend/src/features/reports/AgentReportPage.tsx`
- Test: `frontend/src/features/reports/AgentReportPage.test.tsx`

**Interfaces:**
- Consumes: stored narrative plus `facts.excluded_findings`.
- Produces: fact-derived displayed analyses and the “允许同步” warning label.

- [ ] **Step 1: Write the failing historical-report test**

Mock a completed report whose stored model narrative says three included students were excluded, while the corresponding fact has `inclusion_state: "included"`. Assert the UI displays the included warning, retains the count, and hides the false exclusion/retry text.

- [ ] **Step 2: Run the test to verify RED**

Run: `npm test -- --run src/features/reports/AgentReportPage.test.tsx`

Expected: FAIL because the stale model narrative is rendered verbatim.

- [ ] **Step 3: Implement the view canonicalizer**

Build included warning overrides from frozen facts, replace narrative items by reason code, render a “允许同步” tag, and use “数据质量提醒与排除项” when included warnings are present. Do not alter actual exclusion items.

- [ ] **Step 4: Run frontend tests to verify GREEN**

Run: `npm test -- --run src/features/reports/AgentReportPage.test.tsx`

Expected: PASS.

### Task 3: Verify and commit

**Files:**
- Verify all files modified by Tasks 1 and 2.

**Interfaces:**
- Consumes: completed backend and frontend changes.
- Produces: reviewed, committed worktree branch.

- [ ] **Step 1: Run related suites and static checks**

Run backend related tests, `ruff check .`, `mypy app`, frontend `npm test -- --run`, `npm run lint`, `npm run typecheck`, and `npm run build`.

- [ ] **Step 2: Run full backend verification**

Run: `/Users/lbs/PycharmProjects/PythonProject/backend/.venv/bin/pytest -q`

Expected: all configured tests pass; environment-dependent tests may remain skipped.

- [ ] **Step 3: Review and commit**

Run `git diff --check`, request read-only code review, address Critical/Important findings, and commit with `fix: report included source quality warnings accurately`.
