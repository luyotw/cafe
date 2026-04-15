#!/usr/bin/env bash
# sync_github.sh - Sync confirmed spec/plan output.md to GitHub issue comment
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

if ! command -v python3 >/dev/null 2>&1; then
  echo "Error: python3 is required." >&2
  exit 1
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "Error: gh CLI is required for GitHub sync." >&2
  exit 1
fi

if ! gh auth status >/dev/null 2>&1; then
  echo "Error: gh CLI is not authenticated." >&2
  exit 1
fi

ISSUE_DIR=$(python3 - "$OUTPUT_FILE" <<'PY'
from pathlib import Path
import sys
out = Path(sys.argv[1]).resolve()
print(out.parents[2])
PY
)

ISSUE_YAML="$ISSUE_DIR/issue.yaml"
if [[ ! -f "$ISSUE_YAML" ]]; then
  echo '{"action":"skipped","reason":"issue_yaml_missing"}'
  exit 0
fi

READ_RESULT=$(python3 - "$ISSUE_YAML" "$PHASE" <<'PY'
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

SYNC_ENABLED=$(python3 -c 'import json,sys; print("true" if json.load(sys.stdin).get("sync_enabled") else "false")' <<<"$READ_RESULT")
ISSUE_ID=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("issue_id",""))' <<<"$READ_RESULT")

if [[ "$SYNC_ENABLED" != "true" ]]; then
  echo '{"action":"skipped","reason":"sync_disabled"}'
  exit 0
fi

if [[ -z "$ISSUE_ID" ]]; then
  echo '{"action":"skipped","reason":"missing_issue_id"}'
  exit 0
fi

CONTENT=$(python3 - "$OUTPUT_FILE" <<'PY'
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

BODY=$(python3 - "$HEADER" "$CONTENT" <<'PY'
import json
import sys
header = sys.argv[1]
content = json.loads(sys.argv[2])
print(f"{header}\n\n{content}")
PY
)

gh issue comment "$ISSUE_ID" --body "$BODY" >/dev/null
echo '{"action":"commented","phase":"'"$PHASE"'","issue_id":"'"$ISSUE_ID"'"}'
