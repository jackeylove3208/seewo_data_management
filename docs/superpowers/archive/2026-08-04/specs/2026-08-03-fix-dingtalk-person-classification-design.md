# Fix DingTalk person classification

## Goal

Make DingTalk `people` and `all` connection tests classify every visible person as either
`teacher` or `student`, including people with neutral root memberships, multiple memberships, or
non-standard administrative-unit names. Remove redundant organization reads so connection testing
does not repeat the most expensive DingTalk directory work.

## Root causes

The current classifier permits `unknown` for neutral administrative units, then rejects the whole
connection whenever any personnel-bearing unit remains unclassified. A person commonly belongs to
both a clear branch such as `学生` or `教职工` and a neutral school root. The neutral root therefore
causes a false failure even though the person's other membership establishes a unique identity.

Connection testing also reads the complete department tree and personnel directory twice. The
first read builds the safe classification input; after the model returns, `test_connection` starts
a second capture that authenticates again and repeats the department and personnel requests. Model
repair can add up to three sequential model calls. Department-only tests skip these paths, which
explains the observed latency difference.

## Classification contract

DingTalk person classification is a mandatory binary decision. The persisted mapping and every
captured person use only `teacher` or `student`; `unknown` is not a valid final result.

Classification proceeds in two layers:

1. Deterministic organization-path rules resolve explicit teacher/staff and student branches. A
   neutral ancestor or additional neutral membership cannot override an explicit result.
2. The LLM receives the safe administrative-unit hierarchy and the unresolved membership path
   combinations. It makes the final `teacher` or `student` decision for combinations with no
   explicit result or with both teacher and student evidence.

The LLM classifies membership path combinations, not personal attributes. Its input contains only
department IDs, names, parent IDs, normalized paths, and deduplicated department-membership ID
sets. It never receives names, user IDs, phone numbers, email addresses, job numbers, student
numbers, credentials, or raw DingTalk responses.

Every unique membership combination must resolve to exactly one binary kind. The backend validates
complete coverage, known department IDs, allowed labels, and consistent reuse of the same decision
for duplicate combinations. Invalid or incomplete model output uses the existing bounded repair
policy. Provider/model unavailability remains a safe connection failure because inventing a class
without either deterministic evidence or a model decision would violate the mandatory
classification contract.

## Snapshot reuse and latency

The adapter returns one private in-memory organization snapshot containing the safe inspection and
the already-read person records. Connection testing classifies that snapshot and summarizes the
same snapshot without authenticating or reading DingTalk a second time. The snapshot is limited to
the current request and is never persisted with raw person data.

Deterministically resolved membership combinations do not require an LLM decision. When every
person is resolved by explicit organization paths, the connection test performs no classification
model call. Otherwise the unresolved combinations are submitted together in one request, with the
existing bounded repair attempts used only for malformed output.

The validated department and membership decisions, tree fingerprint, hashes, model evidence, and
classification version are persisted as server-owned configuration. Later task capture continues
to reuse the frozen classification without another model call.

## Error handling

Neutral roots and neutral secondary memberships no longer produce the message that administrative
units cannot be classified. A connection is invalid only for actionable failures such as DingTalk
authentication or permission errors, malformed organization data, contradictory/incomplete model
output after bounded repair, model unavailability when unresolved combinations exist, or an
organization-tree change before capture.

The user-facing error for model failure describes a temporary classification-service failure and
allows retry. It does not incorrectly instruct the operator to reorganize clearly labelled DingTalk
departments.

## Components

- The DingTalk adapter builds and consumes a request-scoped organization snapshot.
- The organization classifier applies deterministic path evidence, asks the model only for
  unresolved membership combinations, validates mandatory binary output, and returns frozen
  department/membership decisions.
- The connection service orchestrates one snapshot read, classification, persistence, and summary.
- Existing task materialization and capture continue emitting canonical `teacher` and `student`
  records without introducing a generic person type.

## Testing

Automated coverage will verify:

- a person under `学校根节点 + 学生` resolves to `student` without failure;
- a person under `学校根节点 + 教职工` resolves to `teacher` without failure;
- neutral memberships do not override a clear classification;
- neither-kind and both-kind membership combinations receive a mandatory binary LLM decision;
- multiple people with the same membership combination reuse one decision;
- model input contains organization metadata and membership IDs only, with no personal fields;
- complete explicit organization data skips the model call;
- people/all connection testing reads each department and personnel page only once;
- malformed model output is repaired and never produces a persisted `unknown` value;
- frozen classification is reused during later capture;
- department-only behavior remains unchanged and never reads people or invokes the classifier.

## Out of scope

This fix does not add manual per-person editing, use personal attributes for inference, change
DingTalk organization membership, introduce a third person kind, or weaken authentication,
permission, pagination, and organization-change checks.
