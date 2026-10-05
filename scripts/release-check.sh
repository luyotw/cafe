#!/bin/bash

# Complete local release gate. This intentionally installs the built wheel in
# a clean environment so undeclared runtime dependencies cannot hide behind the
# development environment or lockfile.

set -euo pipefail

PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$PROJECT_ROOT"

REPORT_ROOT="${CAFE_TEST_REPORT_DIR:-$PROJECT_ROOT/.cafe/reports}"
mkdir -p "$REPORT_ROOT"
REPORT_ROOT=$(cd "$REPORT_ROOT" && pwd)
export CAFE_TEST_REPORT_DIR
CAFE_TEST_REPORT_DIR=$(mktemp -d "$REPORT_ROOT/release-check.XXXXXXXX")
TIMINGS_REPORT="$CAFE_TEST_REPORT_DIR/timings.json"
REPORT_TOOL="$PROJECT_ROOT/scripts/release-report.py"
RELEASE_STARTED=$(python3 "$REPORT_TOOL" clock)
STAGE_NAME=""
STAGE_STARTED=""
RELEASE_TEMP_DIR=""
python3 "$REPORT_TOOL" init --report "$TIMINGS_REPORT"

finish_stage() {
    if [ -n "$STAGE_NAME" ]; then
        python3 "$REPORT_TOOL" stage --report "$TIMINGS_REPORT" \
            --name "$STAGE_NAME" --started "$STAGE_STARTED" --status "$1"
    fi
}

start_stage() {
    finish_stage 0
    STAGE_NAME="$1"
    STAGE_STARTED=$(python3 "$REPORT_TOOL" clock)
}

finish_release() {
    local release_status=$?
    trap - EXIT
    finish_stage "$release_status" || true
    python3 "$REPORT_TOOL" finish --report "$TIMINGS_REPORT" \
        --started "$RELEASE_STARTED" --status "$release_status" \
        --latest "$REPORT_ROOT/release-check-latest.json" || true
    if [ -n "$RELEASE_TEMP_DIR" ]; then
        rm -rf -- "$RELEASE_TEMP_DIR"
    fi
    exit "$release_status"
}
trap finish_release EXIT

# Apply before tests too: verification must not sync globally installed skills.
export CAFE_SKIP_GLOBAL_SKILL_SYNC=1

start_stage coverage
echo "Running release coverage gate (representative kickoff journeys)..."
CAFE_RELEASE_FAST_TESTS=1 ./scripts/test-coverage.sh

start_stage extended
echo "Running the remaining kickoff journeys without coverage instrumentation..."
./scripts/test-extended-kickoff.sh

start_stage contracts
echo "Running builtin contract validation..."
uv run cafe audit >/dev/null
uv run cafe skill validate --strict >/dev/null

start_stage lint
echo "Running critical lint checks..."
uv run ruff check src tests --select E9,F63,F7,F82

RELEASE_TEMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/cafe-release.XXXXXX")
DIST_DIR="$RELEASE_TEMP_DIR/dist"
SMOKE_VENV="$RELEASE_TEMP_DIR/venv"
SMOKE_CWD="$RELEASE_TEMP_DIR/smoke"

start_stage build
echo "Building wheel and source distribution..."
uv build --out-dir "$DIST_DIR"

wheels=("$DIST_DIR"/*.whl)
if [ "${#wheels[@]}" -ne 1 ] || [ ! -f "${wheels[0]}" ]; then
    echo "Expected exactly one wheel in $DIST_DIR" >&2
    exit 1
fi

start_stage install
echo "Installing wheel in a clean environment..."
uv venv "$SMOKE_VENV" >/dev/null
uv pip install --python "$SMOKE_VENV/bin/python" "${wheels[0]}" >/dev/null
uv pip check --python "$SMOKE_VENV/bin/python"

mkdir -p "$SMOKE_CWD"
cd "$SMOKE_CWD"

start_stage package_checks
expected_version=$(
    "$SMOKE_VENV/bin/python" - "$PROJECT_ROOT/pyproject.toml" <<'PY'
import sys
import tomllib
from pathlib import Path

metadata = tomllib.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(metadata["project"]["version"])
PY
)
actual_version=$("$SMOKE_VENV/bin/cafe" version)
case "$actual_version" in
    *"$expected_version") ;;
    *)
        echo "Installed CLI version mismatch: $actual_version" >&2
        exit 1
        ;;
esac

"$SMOKE_VENV/bin/cafe" --help >/dev/null

echo "Verifying packaged Manager and diagnostic chat commands..."
"$SMOKE_VENV/bin/python" - "$SMOKE_VENV/bin/cafe" <<'PYCODE'
import subprocess
import sys

for arguments, options in (
    (["manager", "chat", "--help"], ("--issue",)),
    (["chat", "--help"], ("--read-only", "--phase", "--prompt")),
):
    result = subprocess.run([sys.argv[1], *arguments], capture_output=True, text=True, check=True)
    for option in options:
        if option not in result.stdout:
            raise SystemExit(f"Installed command {arguments!r} is missing {option}")
PYCODE

echo "Verifying packaged subagent planning playbooks..."
for playbook in subagent-flow subagent-flow-qa; do
    "$SMOKE_VENV/bin/cafe" playbook validate "$playbook" --strict >/dev/null
done

echo "Verifying packaged runtime constraints and generated documentation..."
"$SMOKE_VENV/bin/python" - "$SMOKE_VENV/bin/cafe" <<'PYCONSTRAINTS'
import json
import subprocess
import sys

for arguments in (
    ["constraints", "list", "--json"],
    ["constraints", "show", "agent.stdout-idle", "--cli", "codex",
     "--platform", "linux", "--operation", "managed", "--capability", "long-command", "--json"],
):
    result = subprocess.run([sys.argv[1], *arguments], capture_output=True, text=True, check=True)
    payload = json.loads(result.stdout)
    if not any(entry["id"] == "agent.stdout-idle" for entry in payload["entries"]):
        raise SystemExit("Installed constraints registry is missing the stdout idle contract")
PYCONSTRAINTS
"$SMOKE_VENV/bin/cafe" constraints docs check "$PROJECT_ROOT/docs/known-constraints.md"

"$SMOKE_VENV/bin/cafe" audit >/dev/null
"$SMOKE_VENV/bin/cafe" skill validate --strict >/dev/null

echo "Release checks passed for cafe-engine $expected_version."
