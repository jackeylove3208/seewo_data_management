# Conventions
- Python: Ruff line length 100, target 3.12; strict mypy for `app`; focused modules with descriptive domain names.
- Tests mirror source boundaries; test observable behavior and include failure, partial execution, idempotent retry, audit, and rollback-conflict cases.
- Fixtures must use synthetic organization data only.
- OpenSpec change names: lowercase kebab-case.
- Markdown: concise, sentence-case headings, paths/commands in backticks.
- Commits: Conventional Commits with imperative summary (`feat:`, `fix:`, `docs:`, `test:`).
- Preserve unrelated dirty-worktree changes; use `apply_patch` for manual file edits.
- Runtime Agent Skills under `backend/app/ai/skills/*/SKILL.md` use the repository's custom registry contract (`name`, `version`, optional `phase`, `allowed_tools`, optional `input_schema`, `output_schema`), not generic Codex Skill frontmatter.