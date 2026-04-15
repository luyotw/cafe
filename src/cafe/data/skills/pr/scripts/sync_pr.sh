#!/usr/bin/env bash
# sync_pr.sh — Push branch and create/update GitHub PR from output.md
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
  echo '{"action":"updated","pr_number":"'"$PR_NUMBER"'","pr_url":"'"$PR_URL"'"}'
else
  CREATE_ARGS=(--title "$TITLE" --body "$BODY")
  if [[ -n "$BASE_BRANCH" ]]; then
    CREATE_ARGS+=(--base "$BASE_BRANCH")
  fi
  echo "Creating PR..." >&2
  PR_URL=$(gh pr create "${CREATE_ARGS[@]}")
  PR_NUMBER=$(echo "$PR_URL" | grep -oE '[0-9]+$')
  echo '{"action":"created","pr_number":"'"$PR_NUMBER"'","pr_url":"'"$PR_URL"'"}'
fi
