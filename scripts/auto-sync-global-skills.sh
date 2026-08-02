#!/bin/bash

# Verify managed global helper skills after Git changes the current checkout.

set -euo pipefail

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
cd "$REPO_ROOT"

SYNC_PYTHON=${CAFE_GLOBAL_SYNC_PYTHON:-}
if [ -z "$SYNC_PYTHON" ]; then
    if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
        SYNC_PYTHON="$REPO_ROOT/.venv/bin/python"
    else
        COMMON_GIT_DIR=$(git rev-parse --git-common-dir)
        case "$COMMON_GIT_DIR" in
            /*) ;;
            *) COMMON_GIT_DIR="$REPO_ROOT/$COMMON_GIT_DIR" ;;
        esac
        MAIN_WORKTREE=$(cd "$(dirname "$COMMON_GIT_DIR")" && pwd -P)
        if [ -n "$MAIN_WORKTREE" ] && [ -x "$MAIN_WORKTREE/.venv/bin/python" ]; then
            SYNC_PYTHON="$MAIN_WORKTREE/.venv/bin/python"
        else
            SYNC_PYTHON=$(command -v python3 || true)
        fi
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

"$SYNC_PYTHON" -m cafe.skills.global_sync_hook
