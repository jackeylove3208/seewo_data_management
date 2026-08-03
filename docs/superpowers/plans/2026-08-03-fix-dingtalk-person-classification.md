# Fix DingTalk Person Classification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Classify every DingTalk person as `teacher` or `student`, use the LLM only for unresolved membership combinations, and avoid reading the DingTalk organization twice during connection testing.

**Architecture:** The classifier first derives explicit branch evidence and produces a mandatory decision for every canonical membership combination. Ambiguous or evidence-free combinations are sent to a strict binary LLM schema. The DingTalk adapter exposes a request-scoped snapshot so the connection service can classify and summarize the same departments and users without another provider round trip.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, httpx, SQLAlchemy, pytest, Ruff, mypy.

## Global Constraints

- Final person kinds are exactly `teacher` or `student`; persisted output never contains `unknown`.
- The model receives organization metadata and deduplicated membership department IDs only, never personal attributes or credentials.
- Explicit organization evidence skips model work; both-kind and neither-kind combinations require a model decision.
- Raw DingTalk people remain request-scoped and are never persisted in the organization snapshot.
- Department-only behavior remains unchanged.

---

### Task 1: Mandatory binary membership classifier

**Files:**
- Modify: `backend/app/api_connectors/organization_unit_classifier.py`
- Modify: `backend/app/ai/skills/classify-dingtalk-organization-units/SKILL.md`
- Modify: `backend/tests/unit/api_connectors/test_organization_unit_classifier.py`
- Modify: `backend/tests/unit/ai/test_agent_skill_content.py`

**Interfaces:**
- Produces: `canonical_membership_key(memberships: tuple[str, ...]) -> str`.
- Produces: `OrganizationClassificationResult.person_membership_entity_kinds: dict[str, Literal["teacher", "student"]]`.
- Consumes: `OrganizationInspection.departments` and `OrganizationInspection.personnel_memberships`.

- [ ] **Step 1: Write failing classifier tests**

Add tests where `("1", "20")` resolves to student despite the neutral root, `("1", "10")` resolves to teacher, explicit memberships cause zero provider calls, and both/neither evidence produces one model request whose payload has membership IDs but no personal data. Assert every persisted value is binary.

- [ ] **Step 2: Verify the focused tests fail for the current behavior**

Run: `cd backend && .venv/bin/pytest tests/unit/api_connectors/test_organization_unit_classifier.py -q`

Expected: failures show the current classifier rejects the neutral root and expects department-level `unknown` output.

- [ ] **Step 3: Implement deterministic evidence and binary LLM fallback**

Introduce exact normalized explicit branch markers for teacher/staff and student units. Expand explicit evidence through descendants, resolve each deduplicated membership combination, and submit only unresolved combinations to a response schema shaped as:

```python
class _MembershipClassificationItem(BaseModel):
    membership_key: str
    entity_kind: Literal["teacher", "student"]

class _ClassificationResponse(BaseModel):
    classifications: tuple[_MembershipClassificationItem, ...] = Field(min_length=1)
```

Require exact unresolved-key coverage, retain bounded JSON repair, and return an empty attempt tuple when deterministic evidence resolves all combinations. Update the Skill to version `2.0.0`, remove `unknown` from its output contract, and describe binary decisions for both/neither evidence.

- [ ] **Step 4: Verify classifier and Skill tests pass**

Run: `cd backend && .venv/bin/pytest tests/unit/api_connectors/test_organization_unit_classifier.py tests/unit/ai/test_agent_skill_content.py -q`

Expected: all focused tests pass with no warnings.

- [ ] **Step 5: Commit the classifier change**

```bash
git add backend/app/api_connectors/organization_unit_classifier.py backend/app/ai/skills/classify-dingtalk-organization-units/SKILL.md backend/tests/unit/api_connectors/test_organization_unit_classifier.py backend/tests/unit/ai/test_agent_skill_content.py
git commit -m "fix: require binary DingTalk person classification"
```

### Task 2: Persist and consume membership decisions

**Files:**
- Modify: `backend/app/api_connectors/dingtalk_configuration.py`
- Modify: `backend/app/api_connectors/provider_runtime.py`
- Modify: `backend/app/api_connectors/service.py`
- Modify: `backend/tests/unit/api_connectors/test_dingtalk_configuration.py`
- Modify: `backend/tests/unit/api_connectors/test_provider_runtime.py`
- Modify: `backend/tests/integration/api/test_api_connectors.py`

**Interfaces:**
- Consumes: `OrganizationClassificationResult.person_membership_entity_kinds` and `canonical_membership_key` from Task 1.
- Produces: server-owned configuration key `person_membership_entity_kinds`.
- Produces: `person_kind(configuration, memberships)` checks the exact canonical membership decision before department fallback.

- [ ] **Step 1: Write failing configuration and runtime tests**

Assert browser-submitted `person_membership_entity_kinds` is rejected and redacted. Assert `person_kind` resolves `("1", "20")` and a model-decided `("10", "20")` from the canonical membership map without raising an ambiguous-membership error.

- [ ] **Step 2: Verify the focused tests fail**

Run: `cd backend && .venv/bin/pytest tests/unit/api_connectors/test_dingtalk_configuration.py tests/unit/api_connectors/test_provider_runtime.py -q`

Expected: failures show the new server-owned key and membership-level lookup are absent.

- [ ] **Step 3: Implement server-owned persistence and lookup**

Add `person_membership_entity_kinds` to the forbidden/redacted server configuration keys. Persist the classifier's complete membership map alongside `department_entity_kinds`. In `person_kind`, canonicalize the supplied memberships, return the matching binary membership decision when present, and preserve the existing department-map path for frozen legacy configurations.

- [ ] **Step 4: Update integration fixtures and verify persistence**

Update `FakeClassificationProvider` to the version-2 membership response for unresolved cases, and assert connection records persist both maps while safe API views redact both maps.

Run: `cd backend && .venv/bin/pytest tests/unit/api_connectors/test_dingtalk_configuration.py tests/unit/api_connectors/test_provider_runtime.py tests/integration/api/test_api_connectors.py -q`

Expected: all selected tests pass.

- [ ] **Step 5: Commit membership persistence**

```bash
git add backend/app/api_connectors/dingtalk_configuration.py backend/app/api_connectors/provider_runtime.py backend/app/api_connectors/service.py backend/tests/unit/api_connectors/test_dingtalk_configuration.py backend/tests/unit/api_connectors/test_provider_runtime.py backend/tests/integration/api/test_api_connectors.py
git commit -m "fix: persist DingTalk membership decisions"
```

### Task 3: Reuse one DingTalk organization snapshot

**Files:**
- Modify: `backend/app/api_connectors/dingtalk.py`
- Modify: `backend/app/api_connectors/service.py`
- Modify: `backend/tests/contract/test_organization_api_adapters.py`
- Modify: `backend/tests/integration/api/test_api_connectors.py`

**Interfaces:**
- Produces: `DingTalkOrganizationSnapshot` containing an `OrganizationInspection` and request-scoped `_DingTalkUser` records.
- Produces: `inspect_organization_snapshot(configuration, secret) -> DingTalkOrganizationSnapshot`.
- Produces: `test_connection_snapshot(configuration, snapshot) -> ConnectionTestResult`.

- [ ] **Step 1: Write failing single-read regression tests**

Instrument the synthetic DingTalk handler to count token, department, and user-list requests. Assert a people connection test builds one snapshot and never performs a second token, tree, or user traversal. Extend the integration fake with snapshot and snapshot-test call counters.

- [ ] **Step 2: Verify the regression tests fail**

Run: `cd backend && .venv/bin/pytest tests/contract/test_organization_api_adapters.py tests/integration/api/test_api_connectors.py -q`

Expected: the current service calls `inspect_organization` and then the ordinary connection test, proving duplicate work.

- [ ] **Step 3: Implement the request-scoped snapshot path**

Factor organization reading into `inspect_organization_snapshot`. Derive the safe `OrganizationInspection` from the snapshot users. Factor capture projection into a helper that can consume already-read departments/users. `test_connection_snapshot` summarizes this helper without authenticating or calling DingTalk. Keep ordinary `capture` unchanged for later frozen task execution and tree-change validation.

- [ ] **Step 4: Route people/all connection tests through the snapshot**

In `ApiConnectionService.test`, use the snapshot API only for task-ephemeral DingTalk `people`/`all`: read once, classify, persist both maps and audit evidence, then summarize that snapshot. Retain the existing generic `adapter.test_connection` path for department, legacy DingTalk, and other providers.

- [ ] **Step 5: Verify single-read and connector regressions**

Run: `cd backend && .venv/bin/pytest tests/contract/test_organization_api_adapters.py tests/integration/api/test_api_connectors.py tests/integration/api_connectors/test_api_authority_materializer.py -q`

Expected: all tests pass; request counters demonstrate a single provider traversal during people/all connection testing.

- [ ] **Step 6: Commit snapshot reuse**

```bash
git add backend/app/api_connectors/dingtalk.py backend/app/api_connectors/service.py backend/tests/contract/test_organization_api_adapters.py backend/tests/integration/api/test_api_connectors.py
git commit -m "perf: reuse DingTalk organization snapshot"
```

### Task 4: User-facing errors and full verification

**Files:**
- Modify: `frontend/src/features/task-create/ConversationApiConnectionCard.tsx`
- Modify: `frontend/src/features/task-create/ConversationApiConnectionCard.test.tsx`

**Interfaces:**
- Consumes: existing safe error code `connector_entity_classification_unavailable`.
- Produces: retry-oriented Chinese copy that does not instruct users to reorganize clearly labelled units.

- [ ] **Step 1: Write a failing copy regression test**

Render the connection error and assert the message describes a temporary classification-service failure and retry action, with no `调整钉钉组织归属` text.

- [ ] **Step 2: Verify the frontend test fails**

Run: `cd frontend && npm test -- --run src/features/task-create/ConversationApiConnectionCard.test.tsx`

Expected: the old organization-adjustment message causes the assertion to fail.

- [ ] **Step 3: Update the safe Chinese copy**

Map classification unavailability/invalid output to `人员分类服务暂时不可用，请稍后重试连接。` Retain distinct authentication and permission messages.

- [ ] **Step 4: Run focused and repository quality gates**

Run:

```bash
cd backend
.venv/bin/pytest tests/unit/api_connectors/test_organization_unit_classifier.py tests/unit/api_connectors/test_dingtalk_configuration.py tests/unit/api_connectors/test_provider_runtime.py tests/contract/test_organization_api_adapters.py tests/integration/api/test_api_connectors.py tests/integration/api_connectors/test_api_authority_materializer.py -q
.venv/bin/ruff check app/api_connectors tests/unit/api_connectors tests/contract/test_organization_api_adapters.py tests/integration/api/test_api_connectors.py
.venv/bin/mypy app
cd ../frontend
npm test -- --run src/features/task-create/ConversationApiConnectionCard.test.tsx
npm run lint
npm run typecheck
npm run build
```

Expected: every command exits zero.

- [ ] **Step 5: Commit the completed fix**

```bash
git add frontend/src/features/task-create/ConversationApiConnectionCard.tsx frontend/src/features/task-create/ConversationApiConnectionCard.test.tsx
git commit -m "fix: clarify DingTalk classification retry errors"
```
