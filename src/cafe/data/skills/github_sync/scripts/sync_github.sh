#!/usr/bin/env bash
# sync_github.sh - Shared implementation for spec/plan GitHub sync comments
#
# Usage:
#   bash scripts/sync_github.sh --phase spec|plan --output OUTPUT_FILE
#
# Exit codes:
#   0 success (commented or skipped)
#   1 invalid args / runtime error

set -euo pipefail

PHASE=""
OUTPUT_FILE=""
SCRIPT_DIR="$(cd "${BASH_SOURCE[0]%/*}" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../../../.." && pwd)"

resolve_python_bin() {
  if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
    echo "$REPO_ROOT/.venv/bin/python"
    return 0
  fi
  local repo_root
  if repo_root=$(git rev-parse --show-toplevel 2>/dev/null); then
    if [[ -x "$repo_root/.venv/bin/python" ]]; then
      echo "$repo_root/.venv/bin/python"
      return 0
    fi
  fi
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return 0
  fi
  return 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --phase) PHASE="$2"; shift 2 ;;
    --output) OUTPUT_FILE="$2"; shift 2 ;;
    --help)
      echo "Usage: bash scripts/sync_github.sh --phase spec|plan --output OUTPUT_FILE"
      echo ""
      echo "Sync confirmed spec/plan output to GitHub issue comment when enabled in issue.yaml."
      echo ""
      echo "Options:"
      echo "  --phase VALUE    Phase name: spec or plan (required)"
      echo "  --output FILE    Path to confirmed output.md (required)"
      exit 0
      ;;
    *)
      echo "Error: unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

if [[ "$PHASE" != "spec" && "$PHASE" != "plan" ]]; then
  echo "Error: --phase must be 'spec' or 'plan'." >&2
  exit 1
fi

if [[ -z "$OUTPUT_FILE" ]]; then
  echo "Error: --output is required." >&2
  exit 1
fi

if [[ ! -f "$OUTPUT_FILE" ]]; then
  echo "Error: output file not found: $OUTPUT_FILE" >&2
  exit 1
fi

if ! PYTHON_BIN=$(resolve_python_bin); then
  echo "Error: python3 is required." >&2
  exit 1
fi

ISSUE_DIR=$("$PYTHON_BIN" - "$OUTPUT_FILE" <<'PY'
from pathlib import Path
import sys
out = Path(sys.argv[1]).resolve()
# .../<phase>/iteration_xxx/output.md -> issue dir is parent of phase dir
print(out.parents[2])
PY
)

ISSUE_YAML="$ISSUE_DIR/issue.yaml"
if [[ ! -f "$ISSUE_YAML" ]]; then
  echo '{"action":"skipped","reason":"issue_yaml_missing"}'
  exit 0
fi

READ_RESULT=$("$PYTHON_BIN" - "$ISSUE_YAML" "$PHASE" <<'PY'
import json
import sys
from pathlib import Path

import yaml

issue_yaml = Path(sys.argv[1])
phase = sys.argv[2]
data = yaml.safe_load(issue_yaml.read_text(encoding="utf-8")) or {}

spec_cfg = data.get("spec") or {}
phase_cfg = data.get(phase) or {}
issue_id = spec_cfg.get("issue_id")
sync_enabled = bool(phase_cfg.get("sync_github"))

print(json.dumps({
    "issue_id": str(issue_id) if issue_id else "",
    "sync_enabled": sync_enabled,
}))
PY
)

SYNC_ENABLED=$("$PYTHON_BIN" -c 'import json,sys; print("true" if json.load(sys.stdin).get("sync_enabled") else "false")' <<<"$READ_RESULT")
ISSUE_ID=$("$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin).get("issue_id",""))' <<<"$READ_RESULT")

if [[ "$SYNC_ENABLED" != "true" ]]; then
  echo '{"action":"skipped","reason":"sync_disabled"}'
  exit 0
fi

if [[ -z "$ISSUE_ID" ]]; then
  echo '{"action":"skipped","reason":"missing_issue_id"}'
  exit 0
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "Error: gh CLI is required for GitHub sync." >&2
  exit 1
fi

if ! gh auth status >/dev/null 2>&1; then
  echo "Error: gh CLI is not authenticated." >&2
  exit 1
fi

CONTENT=$("$PYTHON_BIN" - "$OUTPUT_FILE" <<'PY'
from pathlib import Path
import json
import sys
print(json.dumps(Path(sys.argv[1]).read_text(encoding="utf-8")))
PY
)

if [[ "$PHASE" == "spec" ]]; then
  HEADER="### 📋 Requirements Specification (Confirmed)"
else
  HEADER="### 📝 Implementation Plan (Confirmed)"
fi

BODY=$("$PYTHON_BIN" - "$HEADER" "$CONTENT" <<'PY'
import json
import sys
header = sys.argv[1]
content = json.loads(sys.argv[2])
print(f"{header}\n\n{content}")
PY
)

gh issue comment "$ISSUE_ID" --body "$BODY" >/dev/null
echo '{"action":"commented","phase":"'"$PHASE"'","issue_id":"'"$ISSUE_ID"'"}'
