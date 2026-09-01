#!/bin/bash

# Explicit coverage gate. Local commit/push hooks stay focused on behavioral
# feedback time; this command owns the slower instrumentation pass.

set -e

PROJECT_PYTHON=".venv/bin/python"
REPORT_DIR=".cafe/reports"
JUNIT_REPORT="$REPORT_DIR/test-durations-latest.xml"

if [ ! -x "$PROJECT_PYTHON" ] || ! "$PROJECT_PYTHON" -c "import pytest" >/dev/null 2>&1; then
    uv sync --extra dev --frozen
fi

export PYTHONPATH="src:$PYTHONPATH"
mkdir -p "$REPORT_DIR"

PYTHON_TMPDIR=$(python3 -c "import tempfile, os; print(os.path.dirname(tempfile.gettempdir()))")
export GIT_CEILING_DIRECTORIES="$PYTHON_TMPDIR"

unset GIT_DIR
unset GIT_WORK_TREE
unset GIT_INDEX_FILE
unset GIT_OBJECT_DIRECTORY
unset GIT_ALTERNATE_OBJECT_DIRECTORIES

"$PROJECT_PYTHON" -m pytest tests/unit/ tests/integration/ -q --tb=short \
    --cov=cafe --cov-report=term-missing --cov-fail-under=75 \
    --durations=50 --durations-min=0.5 \
    --junitxml="$JUNIT_REPORT"

echo "Test durations report: $JUNIT_REPORT"
