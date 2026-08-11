#!/bin/bash

# Complete local release gate. This intentionally installs the built wheel in
# a clean environment so undeclared runtime dependencies cannot hide behind the
# development environment or lockfile.

set -euo pipefail

PROJECT_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$PROJECT_ROOT"

echo "Running coverage gate..."
./scripts/test-coverage.sh

# Release verification must not mutate the caller's globally installed skills.
export CAFE_SKIP_GLOBAL_SKILL_SYNC=1

echo "Running builtin contract validation..."
uv run cafe audit >/dev/null
uv run cafe skill validate --strict >/dev/null

echo "Running critical lint checks..."
uv run ruff check src tests --select E9,F63,F7,F82

RELEASE_TEMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/cafe-release.XXXXXX")
cleanup() {
    rm -rf -- "$RELEASE_TEMP_DIR"
}
trap cleanup EXIT

DIST_DIR="$RELEASE_TEMP_DIR/dist"
SMOKE_VENV="$RELEASE_TEMP_DIR/venv"
SMOKE_CWD="$RELEASE_TEMP_DIR/smoke"

echo "Building wheel and source distribution..."
uv build --out-dir "$DIST_DIR"

wheels=("$DIST_DIR"/*.whl)
if [ "${#wheels[@]}" -ne 1 ] || [ ! -f "${wheels[0]}" ]; then
    echo "Expected exactly one wheel in $DIST_DIR" >&2
    exit 1
fi

echo "Installing wheel in a clean environment..."
uv venv "$SMOKE_VENV" >/dev/null
uv pip install --python "$SMOKE_VENV/bin/python" "${wheels[0]}" >/dev/null
uv pip check --python "$SMOKE_VENV/bin/python"

mkdir -p "$SMOKE_CWD"
cd "$SMOKE_CWD"

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
"$SMOKE_VENV/bin/cafe" audit >/dev/null
"$SMOKE_VENV/bin/cafe" skill validate --strict >/dev/null

echo "Release checks passed for cafe-engine $expected_version."
