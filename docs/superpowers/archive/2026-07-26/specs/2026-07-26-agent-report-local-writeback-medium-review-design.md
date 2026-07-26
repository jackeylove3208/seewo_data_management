# Agent report, local CSV writeback, and medium-risk review design

## Goal

Fix three connected gaps in `agent-graph-v1`:

1. Present the model-generated data synchronization report in the existing Apple blue-black visual language.
2. Let governance and rollback publish verified changes directly to an explicitly authorized local Seewo CSV.
3. Show medium-risk operations before execution with per-item default approval and per-item rejection.

The authoritative third-party CSV remains read-only. Deletion and student-phone updates remain
high risk and require an explicit decision.

## Current root causes

- `AgentReportPage` does not opt into the Apple page theme. Ant Design `Card` and
  `Descriptions` therefore render with their default white surfaces.
- The report model writes `content.narrative.title_zh` and `summary_zh`, but the page ignores
  `content` and renders only reduced server facts.
- Report facts retain only finding IDs, kinds, and categories. They omit safe subject identity,
  analysis, and recommended disposition, making detected records appear missing.
- CSV governance intentionally creates immutable files under `storage/exports/agent-targets`;
  it never publishes the final verified version back to the configured local target path.
- `RECONCILIATION_AGENT_LOCAL_READ_ROOTS` authorizes discovery and bounded reading only.
- Medium-risk create/update operations currently execute automatically without an operator review
  gate.
- `target_extra` permits either `delete` or `retain`; the model can recommend `retain`, so a
  confirmed extra target row may remain unchanged.

## Configuration and file authority

Add:

```env
RECONCILIATION_AGENT_LOCAL_WRITE_ROOTS=[]
```

Rules:

- Every write root must be equal to or contained by a configured local read root.
- A local target CSV must be contained by a write root before a writable task can start.
- An authoritative source is always read-only, even if its physical path happens to be below a
  write root.
- Symlinks, path traversal, blocked directories, non-CSV files, and paths outside configured roots
  remain rejected.
- Browser-uploaded CSVs do not gain access to the user's original desktop path. Direct writeback
  applies only to `kind=local` target selections.
- Public API and report output use the safe relative `source_ref`; arbitrary absolute paths are not
  accepted from the client.
- The local-source API returns only server-discovered source references and whether each reference
  is writable as a target. The external-data-sync page uses this list for direct-writeback tasks;
  the conversation Agent uses the same references. A client cannot mark a read-only reference
  writable.

The intended demo layout is:

```text
docs/sample-data/
├── data/
│   └── agent-third-party-demo.csv
└── seewo/
    └── agent-seewo-demo.csv
```

with:

```env
RECONCILIATION_AGENT_LOCAL_READ_ROOTS=[".../docs/sample-data"]
RECONCILIATION_AGENT_LOCAL_WRITE_ROOTS=[".../docs/sample-data/seewo"]
```

## Immutable versions and direct writeback

The mutable local destination must never be used as an immutable `TargetVersionRecord` path.
When a local task first needs a target version, the backend copies the starting target CSV into
managed version storage and records that copy as the initial version.

Governance continues to:

1. compile frozen operations;
2. apply operations to managed versions;
3. read each result back;
4. persist verification and rollback evidence.

After the final accepted operation batch reaches a stable result, a local publisher:

1. resolves the server-owned target `source_ref`;
2. verifies that it is a target role and lies under a write root;
3. compares the destination hash with the hash last observed or published by this task;
4. copies the latest verified managed version into a temporary file in the destination directory;
5. flushes and `fsync`s the temporary file;
6. atomically replaces the destination with `os.replace`;
7. reads and hashes the destination again;
8. persists a publication record/checkpoint containing safe source reference, expected hash,
   published hash, target version ID, status, and timestamp.

If independent operations partially succeed, the latest verified successful version is published
and the report marks the task partial. If the user terminates after verified mutations, the latest
verified version is published before the termination report. If no mutation succeeded, no file is
replaced.

Hash conflict, missing destination, revoked write authorization, symlink substitution, failed
replacement, or failed readback blocks publication. The backend must not overwrite an externally
changed file and must report a safe conflict requiring operator action.

A rollback task creates a new managed restore version and uses the same publisher to atomically
replace the same authorized local target. It keeps its own lock, audit, report, and history.

## Reconciliation policy for extra rows

For a server-classified `target_extra`, the analysis Skill must return `delete` as the recommended
operation. `retain` is no longer a valid recommended operation for this kind. The server validates
the operation-kind contract instead of relying only on the prompt.

All deletes remain server-classified high risk. A row such as student `孙浩然` is therefore:

```text
target_extra
→ AI analysis and delete proposal
→ high-risk per-item review
→ delete only when explicitly approved
→ retain and report rejection when rejected
```

Correct rows remain silent and never enter the report or approval interface.

## Per-item medium- and high-risk decisions

Display grouping and decision granularity are separate:

- Similar operations are grouped into cards of at most 50 items.
- Every member has its own immutable finding ID and decision.
- Medium-risk items default to approved in the UI, but execution remains paused until the operator
  submits the current selection.
- High-risk items default to pending/unselected and require an explicit approve or reject decision.
- Cards provide `全部同意` and `全部拒绝` shortcuts without changing per-item audit granularity.
- A submitted decision contains an exact partition of frozen member IDs into approved and rejected
  sets and uses the current graph cursor/content hash.
- Rejected findings remain visible, generate no target operation, and appear in the final report.
- An approved operation whose dependency was rejected becomes blocked and appears in the report.
- If every proposed operation is rejected, the workflow skips target execution, generates a
  completed no-change report, and releases the school lock instead of failing plan compilation.

The persisted human gate decision stores member-level outcomes. Plan compilation consumes approved
finding IDs rather than treating a whole approval group as approved. Stale cursor and membership
hash checks remain mandatory.

## Report contract and presentation

The report page uses the Apple blue-black page theme and dark glass surfaces. It renders:

1. model-generated `title_zh` and `summary_zh` as the primary analysis report;
2. translated terminal status and publication status near the title;
3. key fact metrics;
4. a fact appendix of actionable findings;
5. accepted, rejected, blocked, failed, and succeeded governance outcomes;
6. safe local target reference and direct-writeback result;
7. rollback eligibility.

Server report facts add a safe finding view:

- finding kind and Chinese category;
- entity kind;
- safe subject name, number, class, and stable locator;
- analysis text with phone values redacted;
- recommended operation and solution;
- operator decision and execution status;
- changed field names and masked before/after summaries.

The LLM narrative never replaces execution facts. If narrative and facts disagree, the UI preserves
the narrative as an AI summary and labels the server fact appendix as authoritative.

## Error handling

- Missing or malformed model narrative falls back to a server-generated Chinese summary while the
  fact appendix remains available.
- Failed local publication does not claim task completion or release the school lock as if the
  target were updated.
- Repeated publication with the same target version and hash is idempotent.
- Retrying a submitted review with the same cursor and member decisions is idempotent.
- A changed decision, cursor, membership, destination hash, or target version returns a stable
  conflict and performs no duplicate write.

## Test strategy

Backend tests cover:

- write-root parsing, containment, symlink, and role enforcement;
- immutable initial copy for local targets;
- successful atomic publication and readback;
- external file hash conflict and idempotent retry;
- termination and rollback publication;
- `target_extra` requiring a delete recommendation;
- delete and student-phone update classified high risk;
- medium-risk member decisions defaulted only by the UI and persisted per finding;
- mixed approve/reject compilation and rejected dependency blocking;
- report facts containing safe subject and decision details without raw student phone values.

Frontend tests cover:

- Apple dark report surfaces;
- model narrative shown before facts;
- safe finding rows including information center and a target-extra student;
- publication status and local source reference;
- medium-risk items initially checked;
- high-risk items initially unresolved;
- per-item toggles, bulk shortcuts, mixed submission, stale response, and persisted decisions.

Full backend and frontend test, lint, typecheck, and build gates remain required.
