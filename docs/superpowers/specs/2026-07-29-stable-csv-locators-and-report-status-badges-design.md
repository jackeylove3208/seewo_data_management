# Stable CSV locators and report status badges

## Goal

Prevent multi-round CSV synchronization from mutating the wrong row, and make input anomalies and failed executions immediately visible in the synchronization report.

## Root cause

Target records are currently ingested with physical-row locators such as `csv:100`, while generated target versions preserve an `id` column across mutations. Deleting or creating rows changes later physical row numbers without changing the persisted IDs. A later task can therefore approve one business record but execute against a different persisted `id`.

The report has a separate approval decision and execution outcome, but the finding card selects the approval decision first. An approved finding whose execution failed therefore shows only `已同意` and hides `执行失败`.

## Stable locator contract

- Authoritative input keeps physical-row locators because it is read-only within a task.
- Target input uses its non-empty persisted `id` value as `stable_locator`.
- A target CSV without an `id` column continues to use `csv:<physical-row>` during initial ingestion; the execution layer persists those generated IDs in the first derived version.
- Derived target versions preserve existing IDs. Created rows receive a unique ID through the existing execution contract.
- Duplicate or empty persisted target IDs fail closed instead of allowing an ambiguous mutation.

This keeps ingestion, governance planning, CSV lookup, verification, and future synchronization rounds on the same identifier.

## Mutation safeguards

Before a target update or deletion is approved for execution, the row resolved by the stable locator must agree with the analyzed subject on stable business identity fields:

- entity category;
- entity number when present;
- entity name when present.

A mismatch must stop that operation as an input/target-contract anomaly. The executor must never fall back to a different row based only on its current position.

Existing before-value checks and write-after verification remain in place. These safeguards supplement rather than replace them.

## Report presentation

Approval and execution are independent facts and must be rendered independently.

- A finding may show both a green `已同意` tag and a red `执行失败` tag.
- Successful approved findings show both `已同意` and `执行成功`.
- Rejected, blocked, and verification-failed outcomes retain distinct labels and warning/error colors.
- Every excluded-input item is presented as a card with an orange `输入异常` tag in its upper-right corner.
- When any mutation failed or failed verification, the report hero displays `部分完成` instead of implying full success.
- The failed-change summary metric receives an error treatment so users can locate partial execution without scanning the entire report.

## Error reporting

The report remains truthful even when the graph itself reaches its terminal reporting node:

- workflow completion means the report was produced;
- mutation completion is derived from individual operation outcomes;
- any failed operation keeps the plan/report in a partial state;
- approval must never be used as a fallback for a missing execution result.

## Tests

Backend regression coverage must prove:

1. a target record with a persisted `id` is ingested using that ID;
2. deleting an earlier row does not change the locator used to mutate a later row in a subsequent synchronization;
3. a locator whose resolved row disagrees with the analyzed entity fails closed;
4. duplicate persisted IDs are rejected.

Frontend coverage must prove:

1. `已同意` and `执行失败` are simultaneously visible for a failed approved finding;
2. `已同意` and `执行成功` are simultaneously visible for a successful approved finding;
3. input anomalies carry an orange upper-right tag;
4. a report with failed mutations displays `部分完成` and an emphasized failed metric.

## Scope

This change applies to CSV-backed Agent synchronization and the Agent report page. It does not change manual synchronization, SQL connector identifiers, approval policy, rollback policy, or business matching rules.
