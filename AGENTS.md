# AGENTS.md

## Cursor Cloud specific instructions

### Project overview

CAFE (CLI Agent Flow Engine) is a pure Python CLI tool that automates AI-driven development workflows. No backend servers, databases, or containers are needed. See `README.md` for full documentation.

### Development commands

- **Install dependencies**: `uv sync --extra dev`
- **Run CLI**: `uv run cafe --help`
- **Run fast tests** (unit + integration, excluding slow): `uv run pytest tests/unit/ tests/integration/ -q --tb=short --no-cov -m "not slow"`
- **Run full tests** (unit + integration): `uv run pytest tests/unit/ tests/integration/ -q --tb=short`
- **Lint (ruff)**: `uv run ruff check src/ tests/`
- **Format check (black)**: `uv run black --check src/ tests/`
- **Type check (mypy)**: `uv run mypy src/cafe/`

### Testing caveats

- The pre-commit hook (`.githooks/pre-commit`) and pre-push hook (`.githooks/pre-push`) set `GIT_CEILING_DIRECTORIES` and unset git env vars to isolate tests from the real repo. When running tests manually, replicate this by setting `export PYTHONPATH="src:$PYTHONPATH"` and `export GIT_CEILING_DIRECTORIES=$(python3 -c "import tempfile, os; print(os.path.dirname(tempfile.gettempdir()))")`.
- Git hooks are activated via `./setup-hooks.sh` which sets `core.hooksPath` to `.githooks/`. The hooks use `uv run` to execute test commands.
- There are 2 pre-existing test failures in `tests/unit/test_agent_edit_auto_sync.py` and `tests/unit/test_reset_command.py`. These are not caused by environment setup.

### Key notes

- The project uses `uv` as its package manager (has `uv.lock`). Always use `uv run` to execute project commands so the correct virtualenv is used.
- Python 3.10+ is required (`pyproject.toml`). The current environment uses Python 3.12.
- All AI agent CLI tools (claude, copilot, cursor, gemini, codex) are optional and mocked in tests. No real agent CLIs are needed for testing.
