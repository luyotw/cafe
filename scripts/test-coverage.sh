#!/bin/bash

# Explicit coverage gate. Local commit/push hooks stay focused on behavioral
# feedback time; this command owns the slower instrumentation pass.

set -u
set -o pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
cd "$REPO_ROOT"

PROJECT_PYTHON=".venv/bin/python"
REPORT_DIR=".cafe/reports"
JUNIT_REPORT="$REPORT_DIR/test-durations-latest.xml"
TEST_LOG="$REPORT_DIR/test-coverage-latest.log"

notify_test_result() {
    local test_status="$1"
    local notify_python="python3"

    if [ -x "$PROJECT_PYTHON" ]; then
        notify_python="$PROJECT_PYTHON"
    fi
    "$notify_python" "$SCRIPT_DIR/notify-test-result.py" \
        "$test_status" "$SECONDS" "$JUNIT_REPORT" "$TEST_LOG" >/dev/null 2>&1 || true
}

finish() {
    local test_status="$1"
    trap - EXIT
    notify_test_result "$test_status"
    exit "$test_status"
}

trap 'finish "$?"' EXIT

if [ ! -x "$PROJECT_PYTHON" ] || ! "$PROJECT_PYTHON" -c "import pytest" >/dev/null 2>&1; then
    uv sync --extra dev --frozen
fi

export PYTHONPATH="src:${PYTHONPATH:-}"
export CAFE_TEST_RUN_SLACK_NOTIFICATIONS=1
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
    --junitxml="$JUNIT_REPORT" | tee "$TEST_LOG"
test_status=${PIPESTATUS[0]}

echo "Test durations report: $JUNIT_REPORT"
exit "$test_status"
