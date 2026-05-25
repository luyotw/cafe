#!/usr/bin/env bash
# sync_pr.sh — Push branch, create/update GitHub PR, optionally post completed todo list
#
# Usage: bash scripts/sync_pr.sh --output OUTPUT_FILE [--base BASE_BRANCH]
# Exit codes: 0 success, 1 error

set -euo pipefail

OUTPUT_FILE=""
BASE_BRANCH=""
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

if ! PYTHON_BIN=$(resolve_python_bin); then
  echo "Error: python3 is required." >&2
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

ensure_clean_worktree() {
  local dirty
  dirty=$(git status --porcelain)
  if [[ -n "$dirty" ]]; then
    echo "Error: cannot sync PR with uncommitted changes." >&2
    echo "Commit or stash changes first, then run cafe make again." >&2
    echo "$dirty" >&2
    exit 1
  fi
}

post_todo_comment() {
  local pr_number="$1"
  local issue_dir todo_result todo_action comment_body

  issue_dir=$("$PYTHON_BIN" - "$OUTPUT_FILE" <<'PY'
from pathlib import Path
import sys
out = Path(sys.argv[1]).resolve()
print(out.parents[2])
PY
)

  todo_result=$("$PYTHON_BIN" - "$issue_dir" <<'PY'
from pathlib import Path
import json
import re
import sys
import yaml

issue_dir = Path(sys.argv[1])
issue_yaml = issue_dir / "issue.yaml"
if issue_yaml.exists():
    data = yaml.safe_load(issue_yaml.read_text(encoding="utf-8")) or {}
    pr_cfg = data.get("pr") or {}
    post_enabled = pr_cfg.get("post_todo_list")
    if post_enabled is not None and not bool(post_enabled):
        print(json.dumps({"action": "skipped", "reason": "post_todo_list_disabled"}))
        raise SystemExit(0)

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
        "comment_body": f"> 📋 Original review comments: `{user_input}`\n\n{todo_content}",
    }))
    raise SystemExit(0)

print(json.dumps({"action": "skipped", "reason": "todo_iteration_not_found"}))
PY
)

  todo_action=$("$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin).get("action",""))' <<<"$todo_result")
  if [[ "$todo_action" != "ready" ]]; then
    echo "skipped: $("$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin).get("reason","unknown"))' <<<"$todo_result")" >&2
    return 0
  fi

  comment_body=$("$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin).get("comment_body",""))' <<<"$todo_result")
  gh pr comment "$pr_number" --body "$comment_body"
  echo "posted: todo_comment" >&2
}

# Push branch
ensure_clean_worktree
BRANCH=$(git rev-parse --abbrev-ref HEAD)
echo "Pushing branch: $BRANCH" >&2
if ! git push --set-upstream origin "$BRANCH" 2>&1 >&2; then
  echo "Error: failed to push branch '$BRANCH' to origin." >&2
  exit 1
fi

# Create or update PR
EXISTING_PR=$(gh pr view --json number,url,state,baseRefName 2>/dev/null || echo "")

if [[ -n "$EXISTING_PR" ]]; then
  IFS=$'\t' read -r PR_STATE PR_NUMBER PR_URL PR_BASE < <(
    echo "$EXISTING_PR" | "$PYTHON_BIN" -c "import sys,json; d=json.load(sys.stdin); print('\t'.join([d.get('state',''), str(d.get('number','')), d.get('url',''), d.get('baseRefName','')]))"
  )
else
  PR_STATE=""
fi

if [[ "$PR_STATE" == "OPEN" ]]; then
  echo "Updating PR #$PR_NUMBER..." >&2
  gh pr edit "$PR_NUMBER" --title "$TITLE" --body "$BODY" >&2
  post_todo_comment "$PR_NUMBER"
  if [[ -n "$BASE_BRANCH" && "$PR_BASE" != "$BASE_BRANCH" ]]; then
    echo "Retargeting PR #$PR_NUMBER base to $BASE_BRANCH..." >&2
    gh pr edit "$PR_NUMBER" --base "$BASE_BRANCH" >&2
  fi
  echo '{"action":"updated","pr_number":"'"$PR_NUMBER"'","pr_url":"'"$PR_URL"'"}'
else
  CREATE_ARGS=(--title "$TITLE" --body "$BODY" --head "$BRANCH")
  if [[ -n "$BASE_BRANCH" ]]; then
    CREATE_ARGS+=(--base "$BASE_BRANCH")
  fi
  if [[ -n "$PR_STATE" ]]; then
    echo "Existing PR is $PR_STATE; creating a new PR instead..." >&2
  fi
  echo "Creating PR..." >&2
  PR_URL=$(gh pr create "${CREATE_ARGS[@]}")
  PR_NUMBER=$(echo "$PR_URL" | grep -oE '[0-9]+$')
  post_todo_comment "$PR_NUMBER"
  echo '{"action":"created","pr_number":"'"$PR_NUMBER"'","pr_url":"'"$PR_URL"'"}'
fi
