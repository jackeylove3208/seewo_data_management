# Full development launcher implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a tested root-level command that starts and supervises the complete CSV Agent demo.

**Architecture:** A standard-library Python orchestrator builds a non-secret command plan,
performs prerequisite and port checks, runs Docker and migrations synchronously, then supervises
FastAPI, the Agent worker, and Vite. Child processes receive explicit CSV Agent rollout flags
while application secrets remain in ignored `backend/.env`.

**Tech Stack:** Python 3.12 standard library, Docker Compose, Alembic, FastAPI/Uvicorn, React/Vite,
pytest.

## Global constraints

- Never print, copy, generate, or commit `.env` secrets.
- Start only the durable new Agent worker; do not start the legacy AI worker.
- Preserve PostgreSQL on shutdown.
- Fail on occupied ports instead of attaching to an unknown process.
- Implement observable behavior through failing tests before production code.

---

### Task 1: Deterministic launch plan and preflight

**Files:**
- Create: `dev.py`
- Create: `backend/tests/unit/test_dev_launcher.py`

**Interfaces:**
- Produces: `build_launch_plan(root: Path) -> LaunchPlan`
- Produces: `validate_prerequisites(plan: LaunchPlan) -> None`
- Produces: `port_is_available(host: str, port: int) -> bool`

- [ ] **Step 1: Write failing tests**

Test that the plan uses `backend/.venv/bin/python`, starts `app.agent_runtime`, supplies all
three CSV Agent rollout flags, points Compose at `infra/docker-compose.yml`, and rejects a
missing `backend/.env`.

- [ ] **Step 2: Verify the tests fail**

Run:

```bash
cd backend
.venv/bin/pytest tests/unit/test_dev_launcher.py -q
```

Expected: collection fails because root `dev.py` does not exist.

- [ ] **Step 3: Implement the minimal plan and preflight**

Use frozen dataclasses for command and launch-plan values. Check executable/file existence with
`Path`, use `shutil.which` for Docker/npm, and probe ports with `socket.bind`.

- [ ] **Step 4: Verify the focused tests pass**

Run the command from Step 2. Expected: all launcher unit tests pass.

### Task 2: Ordered execution and process supervision

**Files:**
- Modify: `dev.py`
- Modify: `backend/tests/unit/test_dev_launcher.py`

**Interfaces:**
- Produces: `run_checked(command: CommandSpec) -> None`
- Produces: `wait_for_http(url: str, process: Popen, timeout: float) -> None`
- Produces: `DevelopmentSupervisor.run() -> int`

- [ ] **Step 1: Write failing lifecycle tests**

Inject command runners, process factories, and readiness probes. Assert Compose precedes
Alembic, FastAPI becomes ready before the worker and frontend start, and an unexpected exit
terminates sibling processes.

- [ ] **Step 2: Verify the tests fail for missing lifecycle behavior**

Run the focused pytest command and confirm assertion failures describe the missing order.

- [ ] **Step 3: Implement minimal lifecycle behavior**

Run setup commands with `subprocess.run(check=True)`, start child processes with inherited
stdio, wait with bounded polling, and terminate/kill remaining children in `finally`.

- [ ] **Step 4: Verify focused tests pass**

Run the focused pytest command. Expected: all launcher tests pass.

### Task 3: Diagnostics and documentation

**Files:**
- Modify: `dev.py`
- Modify: `README.md`
- Modify: `backend/tests/unit/test_dev_launcher.py`

**Interfaces:**
- Produces: CLI options `--dry-run` and `--no-browser`

- [ ] **Step 1: Write a failing dry-run redaction test**

Assert output lists startup commands and rollout flags without `.env` values or credential
contents.

- [ ] **Step 2: Verify the test fails**

Run the focused pytest command and confirm dry-run behavior is absent.

- [ ] **Step 3: Implement CLI and concise root documentation**

Document `python3 dev.py`, prerequisites, URLs, shutdown behavior, and the ignored `.env`
requirement.

- [ ] **Step 4: Run repository verification**

Run backend pytest/Ruff/mypy, frontend unit/lint/typecheck/build/Playwright, the clean PostgreSQL
migration smoke test, `openspec validate new-agent-architecture`, and `git diff --check`.
