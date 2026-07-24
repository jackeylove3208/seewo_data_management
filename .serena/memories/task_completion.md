# Task completion gates
- Run focused tests during RED/GREEN development, then the relevant full suites.
- Backend CI-equivalent:
```bash
cd backend
.venv/bin/python -m pip install --constraint requirements-ci.txt -e '.[dev]'
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/mypy app
```
- Frontend CI-equivalent:
```bash
cd frontend
npm ci
npm test -- --run
npm run lint
npm run typecheck
npm run build
```
- Run the clean PostgreSQL migration smoke from `mem:suggested_commands` when migrations are affected.
- Validate the relevant OpenSpec change.
- Inspect `git status` and diff; preserve unrelated user work.
- Never claim complete before fresh verification output.