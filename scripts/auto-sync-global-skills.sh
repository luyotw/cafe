#!/bin/bash

# Sync bundled global helper skills only when the compared Git revisions
# changed one of their source directories.

set -euo pipefail

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
cd "$REPO_ROOT"

EMPTY_TREE=$(git hash-object -t tree /dev/null)
BEFORE_REV=${1:-HEAD^}
AFTER_REV=${2:-HEAD}

if [[ "$BEFORE_REV" =~ ^0+$ ]] || ! git rev-parse --verify "$BEFORE_REV^{commit}" >/dev/null 2>&1; then
    BEFORE_REV="$EMPTY_TREE"
fi
git rev-parse --verify "$AFTER_REV^{commit}" >/dev/null 2>&1

CHANGED_FILES=$(git diff --name-only "$BEFORE_REV" "$AFTER_REV" -- \
    src/cafe/data/skills/use-cafe-workflow/ \
    src/cafe/data/skills/write-cafe-playbook/ \
    src/cafe/data/skills/write-cafe-phase/)

if [ -z "$CHANGED_FILES" ]; then
    exit 0
fi

SYNC_PYTHON=${CAFE_GLOBAL_SYNC_PYTHON:-}
if [ -z "$SYNC_PYTHON" ]; then
    if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
        SYNC_PYTHON="$REPO_ROOT/.venv/bin/python"
    else
        SYNC_PYTHON=$(command -v python3 || true)
    fi
fi

if [ -z "$SYNC_PYTHON" ] || [ ! -x "$SYNC_PYTHON" ]; then
    echo "⚠️  Global skill auto-sync skipped: no usable Python interpreter." >&2
    exit 1
fi

if [ -n "${PYTHONPATH:-}" ]; then
    export PYTHONPATH="$REPO_ROOT/src:$PYTHONPATH"
else
    export PYTHONPATH="$REPO_ROOT/src"
fi

echo "🔄 Bundled global helper skills changed; syncing installed copies..."
"$SYNC_PYTHON" -m cafe.ui.cli skill sync-global
