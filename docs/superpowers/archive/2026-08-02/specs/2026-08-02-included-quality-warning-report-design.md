# Included quality warning report design

## Problem

API ingestion correctly marks unavailable authority fields with `inclusion_state=included`. A
student whose only unavailable field is `class_name` therefore remains eligible for identity
matching and synchronization. Report facts nevertheless place every input mark in the legacy
`excluded_findings` collection, and the report model is only validated for reason-code coverage.
The generated narrative can consequently claim that included students were excluded or could not
be matched.

Existing reports are immutable, so correcting only future report generation would leave already
completed tasks misleading.

## Design

The ingestion and governance eligibility rules remain unchanged. Missing student `class_name` is
still recorded as a data-quality anomaly, but it does not exclude the record.

For future reports, the backend will derive the narrative for included
`authority_field_unavailable` marks from frozen facts. It will replace any model-authored analysis
for that reason with deterministic Chinese text stating that the records remained included and
could participate through their available identity fields. The model continues to generate the
rest of the report. Actual excluded or anomalous marks keep their existing behavior.

For existing reports, the frontend will derive the same warning classification from
`facts.excluded_findings[].inclusion_state`. When a stored narrative contradicts an included mark,
the UI displays a deterministic quality-warning explanation instead of the stale model text. The
legacy fact field is retained for compatibility; no report or historical fact is rewritten.

## Presentation

- The missing-field count remains visible.
- Included warnings use an “允许同步” label.
- The impact says the records remained in matching and synchronization scope.
- The suggestion may recommend improving source quality, but must not require rerunning a
  successfully completed task.
- Real exclusions continue to appear as input exceptions or excluded items.

## Tests

- A backend report runner that claims an included unavailable field was excluded is corrected
  before persistence.
- A historical report containing the stale exclusion narrative renders an included quality
  warning and does not render the false exclusion claim.
- Existing excluded/anomalous input reporting remains unchanged.
