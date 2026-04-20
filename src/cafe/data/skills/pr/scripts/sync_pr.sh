#!/usr/bin/env bash
# sync_pr.sh — Push branch, create/update GitHub PR, optionally post completed todo list
#
# Usage: bash scripts/sync_pr.sh --output OUTPUT_FILE [--base BASE_BRANCH]
# Exit codes: 0 success, 1 error

set -euo pipefail

OUTPUT_FILE=""
BASE_BRANCH=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output) OUTPUT_FILE="$2"; shift 2 ;;
    --base)   BASE_BRANCH="$2"; shift 2 ;;
    --help)
      echo "Usage: bash scripts/sync_pr.sh --output OUTPUT_FILE [--base BASE_BRANCH]"
      echo ""
      echo "Push current branch and create or update a GitHub PR using OUTPUT_FILE."
      echo "OUTPUT_FILE must be a markdown file whose first line is '# <PR title>'."
      echo ""
      echo "Options:"
      echo "  --output FILE   Path to the PR output markdown file (required)"
      echo "  --base BRANCH   Base branch for PR creation (default: repo default branch)"
      echo ""
      echo "Exit codes:"
      echo "  0   PR created or updated successfully"
      echo "  1   Error (missing args, missing file, parse failure, git/gh error)"
      exit 0 ;;
    *) echo "Error: unknown argument: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$OUTPUT_FILE" ]]; then
  echo "Error: --output is required." >&2
  echo "Usage: bash scripts/sync_pr.sh --output OUTPUT_FILE [--base BASE_BRANCH]" >&2
  exit 1
fi

if [[ ! -f "$OUTPUT_FILE" ]]; then
  echo "Error: output file not found: $OUTPUT_FILE" >&2
  exit 1
fi

# Parse title from first line (# Title)
FIRST_LINE=$(head -1 "$OUTPUT_FILE")
if [[ ! "$FIRST_LINE" =~ ^#[[:space:]] ]]; then
  echo "Error: output file must start with '# <PR title>', got: $FIRST_LINE" >&2
  exit 1
fi
TITLE="${FIRST_LINE:2}"
TITLE="${TITLE## }"
if [[ -z "$TITLE" ]]; then
  echo "Error: PR title is empty in $OUTPUT_FILE" >&2
  exit 1
fi

# Body = everything after first line
BODY=$(tail -n +2 "$OUTPUT_FILE" | sed '/./,$!d')

post_todo_comment() {
  local pr_number="$1"
  local issue_dir issue_yaml
  local todo_result todo_action todo_body user_input_path
  local post_enabled is_todo has_unchecked

  issue_dir=$(python3 - "$OUTPUT_FILE" <<'PY'
from pathlib import Path
import sys
out = Path(sys.argv[1]).resolve()
print(out.parents[2])
PY
)
  issue_yaml="$issue_dir/issue.yaml"

  post_enabled=$(python3 - "$issue_yaml" <<'PY'
import sys
from pathlib import Path
import yaml

issue_yaml = Path(sys.argv[1])
if not issue_yaml.exists():
    print("true")
    raise SystemExit(0)
data = yaml.safe_load(issue_yaml.read_text(encoding="utf-8")) or {}
pr_cfg = data.get("pr") or {}
value = pr_cfg.get("post_todo_list")
if value is None:
    print("true")
else:
    print("true" if bool(value) else "false")
PY
)

  if [[ "$post_enabled" != "true" ]]; then
    echo "skipped: post_todo_list_disabled" >&2
    return 0
  fi

  todo_result=$(python3 - "$issue_dir" <<'PY'
from pathlib import Path
import json
import re
import sys

issue_dir = Path(sys.argv[1])
pr_dir = issue_dir / "pr"
if not pr_dir.exists():
    print(json.dumps({"action": "skipped", "reason": "pr_dir_missing"}))
    raise SystemExit(0)

for iter_dir in sorted(pr_dir.glob("iteration_*"), reverse=True):
    user_input = iter_dir / "user_input.md"
    output = iter_dir / "output.md"
    if not user_input.exists() or not output.exists():
        continue
    todo_content = output.read_text(encoding="utf-8").strip()
    if not todo_content:
        continue
    is_todo = any(marker in todo_content for marker in ("## Todo List", "## Todo", "- [ ]", "- [x]"))
    if not is_todo:
        continue
    has_unchecked = bool(re.search(r"(?m)^- \[ \] ", todo_content))
    if has_unchecked:
        print(json.dumps({"action": "skipped", "reason": "todo_incomplete"}))
        raise SystemExit(0)
    print(json.dumps({
        "action": "ready",
        "todo_content": todo_content,
        "user_input_path": str(user_input),
    }))
    raise SystemExit(0)

print(json.dumps({"action": "skipped", "reason": "todo_iteration_not_found"}))
PY
)

  todo_action=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("action",""))' <<<"$todo_result")
  if [[ "$todo_action" != "ready" ]]; then
    echo "skipped: $(python3 -c 'import json,sys; print(json.load(sys.stdin).get("reason","unknown"))' <<<"$todo_result")" >&2
    return 0
  fi

  todo_body=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("todo_content",""))' <<<"$todo_result")
  user_input_path=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("user_input_path",""))' <<<"$todo_result")

  gh pr comment "$pr_number" --body "$(python3 - "$todo_body" "$user_input_path" <<'PY'
import sys
todo_content = sys.argv[1]
user_input_path = sys.argv[2]
print(f"> 📋 Original review comments: `{user_input_path}`\n\n{todo_content}")
PY
)"
  echo "posted: todo_comment" >&2
}

# Push branch
BRANCH=$(git rev-parse --abbrev-ref HEAD)
echo "Pushing branch: $BRANCH" >&2
git push --set-upstream origin "$BRANCH" 2>&1 >&2 || true

# Create or update PR
EXISTING_PR=$(gh pr view --json number,url 2>/dev/null || echo "")

if [[ -n "$EXISTING_PR" ]]; then
  PR_NUMBER=$(echo "$EXISTING_PR" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['number'])")
  PR_URL=$(echo "$EXISTING_PR" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['url'])")
  echo "Updating PR #$PR_NUMBER..." >&2
  gh pr edit "$PR_NUMBER" --title "$TITLE" --body "$BODY" >&2
  post_todo_comment "$PR_NUMBER"
  echo '{"action":"updated","pr_number":"'"$PR_NUMBER"'","pr_url":"'"$PR_URL"'"}'
else
  CREATE_ARGS=(--title "$TITLE" --body "$BODY")
  if [[ -n "$BASE_BRANCH" ]]; then
    CREATE_ARGS+=(--base "$BASE_BRANCH")
  fi
  echo "Creating PR..." >&2
  PR_URL=$(gh pr create "${CREATE_ARGS[@]}")
  PR_NUMBER=$(echo "$PR_URL" | grep -oE '[0-9]+$')
  post_todo_comment "$PR_NUMBER"
  echo '{"action":"created","pr_number":"'"$PR_NUMBER"'","pr_url":"'"$PR_URL"'"}'
fi
