# Final review fix report

## Status

All four final-review findings are implemented. The backend canonicalizes future persisted
included-quality-warning titles, summaries, and analyses. The frontend applies the same
fact-bound presentation to immutable historical reports. Ingestion eligibility, APIs, stored
historical reports, and unrelated exclusion/anomaly deduplication were not changed.

## RED evidence

Backend, from `backend/`:

```text
/Users/lbs/PycharmProjects/PythonProject/backend/.venv/bin/pytest tests/integration/agent_graph/test_reporting.py -q
F.FFFF [100%]
5 failed, 1 passed in 0.38s
```

The failures covered unsafe persisted title/summary, mixed wording, pure-overlap warning
creation, exclusive field/entity filtering, and complete field localization. The worktree does
not contain its own `.venv`; an initial `.venv/bin/pytest ...` attempt therefore exited 127 and
was replaced by the repository Python 3.12 environment shown above.

Frontend, from `frontend/`:

```text
npm test -- --run src/features/reports/AgentReportPage.test.tsx
Test Files  1 failed (1)
Tests  5 failed | 9 passed (14)
```

The failures covered historical title/summary escaping, pure overlap, exclusive count/fields,
six-field localization, and mixed-inclusion impact wording.

## Implementation

- Replaces model-authored title and summary with neutral fact-bound copy whenever an included
  `authority_field_unavailable` warning exists.
- Uses a positive exclusive `reason_counts.authority_field_unavailable` as the warning gate and
  count whenever diagnostics exist; `overlapped_reason_counts` never creates the warning.
- Uses only positive `unavailable_field_counts` keys for displayed fields and filters entity
  labels to included marks that contain those fields. Only reports with diagnostics entirely
  absent fall back to included marks.
- Uses mixed-state impact text that distinguishes allowed records from records handled as
  excluded or anomalous, without claiming all affected records are allowed.
- Localizes `category`, `name`, `number`, `class_name`, `phone`, and `email` in both layers.

## GREEN evidence

Backend, from `backend/`:

```text
/Users/lbs/PycharmProjects/PythonProject/backend/.venv/bin/pytest tests/integration/agent_graph/test_reporting.py tests/unit/agent_runtime/test_csv_governance_handlers.py -q
11 passed in 0.90s

/Users/lbs/PycharmProjects/PythonProject/backend/.venv/bin/ruff check app/agent_graph/report_executors.py tests/integration/agent_graph/test_reporting.py
All checks passed!

/Users/lbs/PycharmProjects/PythonProject/backend/.venv/bin/mypy app
Success: no issues found in 255 source files
```

Frontend, from `frontend/`:

```text
npm test -- --run src/features/reports/AgentReportPage.test.tsx
Test Files  1 passed (1)
Tests  14 passed (14)

npm run lint
exit 0

npm run typecheck
exit 0

npm run build
3155 modules transformed; built in 2.54s; exit 0
```

Repository root:

```text
git diff --check
exit 0; no output
```

## Self-review

- Safe copy does not infer success, writeback, exclusion, or a required rerun.
- Pure overlap and the `3` exclusive class records plus `4` overlapped identity-field marks have
  dedicated backend and frontend regressions.
- Actual excluded/anomaly rows remain visible and other reason-code deduplication is unchanged.
- No API schema, ingestion eligibility, migration, persistence rewrite, or dependency changed.

## Concerns

None identified. Verification used the repository's existing Python 3.12 virtual environment
because worktrees do not duplicate `.venv`.

## Follow-up: same-field overlap entity attribution

The final follow-up review identified that matching `affected_fields` alone cannot distinguish an
exclusive included mark from a same-field mark absorbed by a higher-priority anomaly. Both
backend persistence and historical frontend presentation now infer entity labels only when the
candidate evidence can safely fit within the positive exclusive reason count. More candidates
than the exclusive count, no candidates, or missing candidate entity evidence produces the
neutral “记录” label and conservative higher-priority-anomaly impact text. An exact candidate
count remains eligible for precise entity labels. `department` and `teacher` are now localized as
“部门” and “教师” when attribution is unambiguous.

### Follow-up RED evidence

Backend, from `backend/`:

```text
/Users/lbs/PycharmProjects/PythonProject/backend/.venv/bin/pytest tests/integration/agent_graph/test_reporting.py -q
.....FF. [100%]
2 failed, 6 passed in 0.44s
```

Frontend, from `frontend/`:

```text
npm test -- --run src/features/reports/AgentReportPage.test.tsx
Test Files  1 failed (1)
Tests  2 failed | 14 passed (16)
```

Both suites failed because the same-field `student` plus `teacher` candidates were rendered as
`teacher、学生`, and the unambiguous entity-localization fixtures exposed raw `department` and
`teacher` labels.

### Follow-up GREEN evidence

Backend, from `backend/`:

```text
/Users/lbs/PycharmProjects/PythonProject/backend/.venv/bin/pytest tests/integration/agent_graph/test_reporting.py tests/unit/agent_runtime/test_csv_governance_handlers.py -q
13 passed in 0.52s

/Users/lbs/PycharmProjects/PythonProject/backend/.venv/bin/ruff check app/agent_graph/report_executors.py tests/integration/agent_graph/test_reporting.py
All checks passed!

/Users/lbs/PycharmProjects/PythonProject/backend/.venv/bin/mypy app
Success: no issues found in 255 source files
```

Frontend, from `frontend/`:

```text
npm test -- --run src/features/reports/AgentReportPage.test.tsx
Test Files  1 passed (1)
Tests  16 passed (16)

npm run lint
exit 0

npm run typecheck
exit 0

npm run build
3155 modules transformed; built in 2.56s; exit 0
```

Repository root:

```text
git diff --check
exit 0; no output
```

### Follow-up self-review

- The ambiguous fixture preserves exclusive count `1` and field “邮箱”, while omitting both
  “教师” and raw `teacher` and not describing the overlap record as allowed.
- Existing exact-count student attribution, pure-overlap suppression, explicit mixed inclusion,
  and diagnostics-absent historical fallback tests remain green.
- No ingestion eligibility, API contract, historical persistence, or deduplication behavior
  changed.
