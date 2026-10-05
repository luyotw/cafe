#!/bin/bash

# Run the longer kickoff journeys once without coverage instrumentation.

set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
cd "$REPO_ROOT"

PROJECT_PYTHON=".venv/bin/python"
if [ ! -x "$PROJECT_PYTHON" ] || ! "$PROJECT_PYTHON" -c "import pytest, xdist" >/dev/null 2>&1; then
    uv sync --extra dev --frozen
fi

export PYTHONPATH="src:${PYTHONPATH:-}"
PYTHON_TMPDIR=$(python3 -c "import tempfile, os; print(os.path.dirname(tempfile.gettempdir()))")
export GIT_CEILING_DIRECTORIES="$PYTHON_TMPDIR"
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES

TEST_WORKERS="${CAFE_EXTENDED_TEST_WORKERS:-4}"
case "$TEST_WORKERS" in
    ''|*[!0-9]*) echo "CAFE_EXTENDED_TEST_WORKERS must be a non-negative integer" >&2; exit 2 ;;
esac
REPORT_DIR="${CAFE_TEST_REPORT_DIR:-.cafe/reports}"
mkdir -p "$REPORT_DIR"

"$PROJECT_PYTHON" -m pytest \
    tests/unit/test_kickoff_catalog.py \
    tests/integration/test_kickoff_preparation.py \
    tests/integration/test_kickoff_review_journeys.py \
    -m 'release_extended and not release_smoke' -q --tb=short --no-cov \
    -n "$TEST_WORKERS" --dist=worksteal --durations=25 --durations-min=0.5 \
    --junitxml="$REPORT_DIR/test-extended-durations-latest.xml" \
    | tee "$REPORT_DIR/test-extended-latest.log"
