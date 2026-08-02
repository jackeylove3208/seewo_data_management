# Retry no-write SQL rollback design

## Problem

SQL rollback treats every configured canonical database field as a required complete-record fact.
Governance facts intentionally omit `class_name` for departments and teachers because that field
applies only to students. A successful delete of a non-student record therefore becomes
`complete_record_fact_missing` during rollback, even when the deleted row is still absent and is
safe to recreate.

The resulting rollback task finishes as `completed_with_conflicts`. Its operations all carry
`verification.no_write=true`, but the rollback preview endpoint keeps returning that terminal task
through the original idempotency key, so deploying a comparison fix alone does not let operators
retry historical sync tasks.

## Design

### Entity-aware complete-record comparison

SQL rollback will derive complete-record fields from the frozen database mapping and the mutation's
`entity_kind`. `class_name` remains required for students and is excluded for departments and
teachers. All other canonical and custom allow-listed fields remain required. This preserves the
whole-record safety boundary while recognizing the established entity contract.

The comparison is applied both while planning rollback impact and immediately before each write.
Historical frozen mutation facts are not rewritten.

### Immutable retry attempts

Rollback reports, checkpoints, runs, and tasks remain immutable. When rollback preview finds a
prior terminal attempt for the same source task, target version, and rollback-cycle generation, it
may create a successor attempt only when the prior report is `completed_with_conflicts` and every
mutation is an explicit no-write terminal fact.

The new task records a monotonically increasing `rollback_attempt` and uses an attempt-qualified
idempotency key. Repeated preview requests return the newest pending or active attempt instead of
creating duplicates. A completed rollback remains blocked by the existing rollback-cycle guard.
Any prior attempt with a successful write, an unknown verification shape, or mixed write/no-write
outcomes is not automatically retryable.

## Data flow

1. The operator requests rollback preview from the original successful sync task.
2. The service loads rollback attempts for the same source task and target version.
3. If the newest attempt is active, it is returned. If it is safely retryable, a successor preview
   is created; otherwise the terminal attempt is returned unchanged.
4. Rollback planning compares each current MySQL record with entity-aware complete fields.
5. Execution repeats the same comparison and writes only when the plan and current comparison are
   both `safe_to_restore` with identical hashes.

## Safety and compatibility

- No existing task, report, checkpoint, or mutation fact is modified.
- Student rollback continues to require `class_name`.
- Custom physical columns remain part of complete-record verification.
- Partial or ambiguous rollback attempts cannot be automatically retried.
- Existing first-attempt idempotency keys remain valid.

## Tests

- A historical department delete without `class_name` is safe to restore when the row remains absent.
- A student delete without `class_name` remains a complete-record conflict.
- A terminal all-no-write conflict attempt creates one successor preview.
- Repeated preview calls return the same successor attempt.
- A prior attempt containing any successful write is not automatically retried.
