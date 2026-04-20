#!/usr/bin/env bash
# Wrapper script. Shared implementation is maintained in one place:
# src/cafe/data/skills/shared/scripts/sync_github.sh

set -euo pipefail
SCRIPT_DIR="$(cd "${BASH_SOURCE[0]%/*}" && pwd)"
SHARED_SCRIPT="$SCRIPT_DIR/../../shared/scripts/sync_github.sh"

if [[ ! -f "$SHARED_SCRIPT" ]]; then
  echo "Error: shared sync script not found: $SHARED_SCRIPT" >&2
  exit 1
fi

exec /bin/bash "$SHARED_SCRIPT" "$@"
