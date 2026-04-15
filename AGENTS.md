# AGENTS.md

## Cursor Cloud specific instructions

### Project overview

CAFE (CLI Agent Flow Engine) is a Python CLI tool that orchestrates headless AI agents through a 5-phase development workflow: spec → plan → develop → review → PR. It is a single Python package (not a monorepo) using `uv` with `hatchling` as the build backend.

### Running the application

- `uv run cafe --help` — show all available commands
- `uv run cafe version` — show current version
- The full workflow (`cafe make`) requires at least one AI agent CLI (claude, copilot, cursor, gemini, codex) and `gh` CLI for GitHub integration. These are external dependencies not installed by the update script.

### Lint / Test / Build

Commands are run from the workspace root via `uv run`:

| Task | Command |
|------|---------|
| Lint (ruff) | `uv run ruff check src/ tests/` |
| Format check (black) | `uv run black --check src/ tests/` |
| Type check (mypy) | `uv run mypy src/` |
| Tests | `uv run pytest` |
| Tests (fast, pre-commit) | `uv run pytest -m "not slow"` |
| Tests (full, pre-push) | `uv run pytest` |

The existing codebase has pre-existing lint warnings (ruff ~2135 issues, black formatting diffs, mypy ~265 errors). These are not regressions; do not attempt to fix them unless explicitly asked.

### Gotchas

- The project uses `uv` (with `uv.lock`), not plain `pip`. Always use `uv run <tool>` or `uv sync` rather than `pip install`.
- Dev dependencies are in `[project.optional-dependencies] dev` and installed via `uv sync --extra dev`.
- There is 1 pre-existing test failure in `test_agent_edit_auto_sync.py::test_agent_edit_triggers_sync_after_successful_edit`. This is a known issue in the repository.
- Git hooks are configured via `.githooks/` directory (set up by `./setup-hooks.sh`). The pre-commit hook runs the fast test suite; the pre-push hook runs the full suite including slow tests.
