# Report exception overlap design

## Problem

One authoritative input record can carry both `authority_field_unavailable` and
`authority_identity_absent`. The current report facts count both marks independently, and the
report skill requires one narrative analysis for every reason code. As a result, the same four
department records can appear first in a broad field-missing count and again in an
identity-missing count. The summary, problem analysis, and input-exception section therefore
describe more anomalies than the number of unique affected records.

For the reported example, four department records overlap and three student records do not. The
reader-facing report must describe seven unique anomalous records, not eleven reason occurrences.

## Design

The server will keep raw input marks unchanged for audit and reconciliation behavior. Report
diagnostics will additionally classify marked inputs into mutually exclusive reader-facing
groups. Higher-severity reasons take precedence:

1. `authority_identity_absent`
2. Other excluded or anomalous reasons
3. `authority_field_unavailable`

An input assigned to a higher-priority group is omitted from lower-priority group counts and
field summaries. In the reported example, the four department records belong only to
`authority_identity_absent`; the three student records remain in
`authority_field_unavailable`.

The report fact manifest will expose these mutually exclusive counts as the authoritative source
for narrative totals. Raw `excluded_findings` remain available as audit facts but must not be
summed to produce reader-facing record totals. The report-generation skill will explicitly
require summaries and `input_exception_analyses` to use exclusive counts and to explain distinct
effects without restating a broader group.

The frontend will continue to render the model's structured analyses and will use the explicit
overlap diagnostics for the anomaly metric and raw-fact fallback. It will not infer overlap from
prose. `authority_invalid` findings belong to the input-exception section and are excluded from
the actionable “问题分析与治理方案” list so the same source defect is not presented in both
sections.

## Data flow

1. Ingestion and reconciliation persist all raw marks exactly as today.
2. `build_agent_report_facts` groups marks by input record and selects one reader-facing reason
   according to severity.
3. `input_diagnostics` exposes unique marked-record totals, exclusive reason counts, and
   field counts calculated only from the selected groups.
4. The report model reads those diagnostics and produces distinct analyses whose mentioned
   record counts do not overlap.
5. Report validation verifies complete coverage of the mutually exclusive positive reason
   counts and immutable fact references.
6. The frontend displays `unique_marked_input_count`, suppresses raw reasons identified as
   overlaps, and keeps `authority_invalid` out of the actionable governance list.

## Compatibility and error handling

No database schema or API endpoint changes are required. Existing raw mark facts and reason codes
remain intact. Diagnostics without overlapping marks retain their current counts. If a future
reason has no configured priority, it is treated as an ordinary reason and remains independently
counted.

The model must stop rather than invent a total when exclusive diagnostics are absent or
contradict raw facts. Report validation continues to reject omitted or duplicated reason codes.

## Testing

- Add a fact-builder test where one authoritative record has both field-unavailable and
  identity-absent marks; assert that it contributes only to identity-absent reader-facing counts.
- Add a seven-record scenario with four overlapping departments and three students; assert that
  the exclusive total is seven, with counts `4 + 3`.
- Update report-skill tests to require exclusive diagnostics as the source for narrative totals
  and prohibit summing overlapping raw reason occurrences.
- Add frontend tests for the unique anomaly metric, absorbed raw reason codes, and
  `authority_invalid` section ownership.
- Run focused backend tests, the backend suite, Ruff, and mypy.
