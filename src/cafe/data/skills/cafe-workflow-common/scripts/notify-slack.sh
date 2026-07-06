#!/usr/bin/env bash
# notify-slack.sh - Best-effort Slack workflow notification hook.
#
# Intended for playbook after_execute script hooks filtered by when_intents.
# The webhook URL is read from a local file and must not be committed.

set -euo pipefail

ISSUE=""
STEP=""
NEXT_STEP_FILE=""
BLACKBOARD_FILE=""
SUMMARY=""
TRACE_FILE=""
WEBHOOK_FILE="${CAFE_SLACK_WEBHOOK_FILE:-${HOME:-}/.slack-webhook}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --issue) ISSUE="$2"; shift 2 ;;
    --step) STEP="$2"; shift 2 ;;
    --next-step) NEXT_STEP_FILE="$2"; shift 2 ;;
    --blackboard) BLACKBOARD_FILE="$2"; shift 2 ;;
    --summary) SUMMARY="$2"; shift 2 ;;
    --trace-file) TRACE_FILE="$2"; shift 2 ;;
    --webhook-file) WEBHOOK_FILE="$2"; shift 2 ;;
    --help)
      echo "Usage: notify-slack.sh --step STEP --next-step FILE --blackboard FILE [--issue ISSUE]"
      exit 0
      ;;
    *)
      echo "Error: unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

resolve_python_bin() {
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return 0
  fi
  return 1
}

if ! PYTHON_BIN=$(resolve_python_bin); then
  echo "Error: python3 is required." >&2
  exit 1
fi

PAYLOAD_JSON=$("$PYTHON_BIN" - "$ISSUE" "$STEP" "$NEXT_STEP_FILE" "$BLACKBOARD_FILE" "$SUMMARY" "$TRACE_FILE" <<'PY'
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys


def read_json(path_value: str) -> dict:
    if not path_value:
        return {}
    path = Path(path_value)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


issue_arg, step_arg, next_step_arg, blackboard_arg, summary_arg, trace_arg = sys.argv[1:7]
next_step_path = Path(next_step_arg) if next_step_arg else None
blackboard_path = Path(blackboard_arg) if blackboard_arg else None

contract = read_json(next_step_arg)
blackboard = read_json(blackboard_arg)

issue = issue_arg.strip()
if not issue and blackboard_path is not None:
    issue = blackboard_path.parent.name
if not issue and next_step_path is not None:
    issue = next_step_path.parent.name
if not issue:
    issue = "unknown"

step = step_arg.strip() or str(contract.get("from_step") or "")
intent = str(contract.get("intent") or contract.get("status_code") or "").strip()
summary = str(blackboard.get("handoff_summary") or summary_arg or "").strip()

if trace_arg:
    trace_path = Path(trace_arg)
else:
    issue_dir = None
    if blackboard_path is not None:
        issue_dir = blackboard_path.parent
    elif next_step_path is not None:
        issue_dir = next_step_path.parent
    trace_path = (issue_dir or Path.cwd()) / "artifacts" / "slack_notifications.jsonl"

text = "\n".join(
    [
        "CAFE workflow notification",
        f"Issue: {issue}",
        f"Step: {step or 'unknown'}",
        f"Intent: {intent or 'unknown'}",
        f"Summary: {summary or '(none)'}",
    ]
)

record = {
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "issue": issue,
    "step": step,
    "intent": intent,
    "summary": summary,
    "trace_file": str(trace_path),
}

print(json.dumps({"text": text, "record": record}, ensure_ascii=False))
PY
)

SLACK_TEXT=$("$PYTHON_BIN" -c 'import json,sys; print(json.dumps({"text": json.load(sys.stdin)["text"]}, ensure_ascii=False))' <<<"$PAYLOAD_JSON")
TRACE_FILE=$("$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin)["record"]["trace_file"])' <<<"$PAYLOAD_JSON")

write_trace() {
  local action="$1"
  local reason="$2"
  "$PYTHON_BIN" - "$PAYLOAD_JSON" "$TRACE_FILE" "$action" "$reason" <<'PY'
from __future__ import annotations

import json
from pathlib import Path
import sys

payload = json.loads(sys.argv[1])
trace_file = Path(sys.argv[2])
action = sys.argv[3]
reason = sys.argv[4]
record = payload["record"]
record["action"] = action
if reason:
    record["reason"] = reason
trace_file.parent.mkdir(parents=True, exist_ok=True)
with trace_file.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
print(json.dumps({"action": action, "reason": reason, "trace_file": str(trace_file)}, ensure_ascii=False))
PY
}

if [[ -z "$WEBHOOK_FILE" || ! -f "$WEBHOOK_FILE" ]]; then
  write_trace "skipped" "webhook_file_missing"
  exit 0
fi

WEBHOOK_URL="$(tr -d '\r\n' < "$WEBHOOK_FILE")"
if [[ -z "$WEBHOOK_URL" ]]; then
  write_trace "skipped" "webhook_file_empty"
  exit 0
fi

if ! command -v curl >/dev/null 2>&1; then
  write_trace "skipped" "curl_missing"
  exit 0
fi

if curl -fsS -X POST -H "Content-Type: application/json" --data "$SLACK_TEXT" "$WEBHOOK_URL" >/dev/null 2>&1; then
  write_trace "posted" ""
  exit 0
fi

write_trace "skipped" "curl_failed"
exit 0
