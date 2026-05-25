## Checklist

[ ] Read {agent_file} to understand your role and native language
[ ] Read the requirements specification {spec_file_path}
[ ] Read the implementation plan {plan_file_path}
[ ] Read PR feedback in {pr_feedback_file_path} (if exists) to see user feedback and requests
[ ] Prioritize user feedback from PR comments over spec requirements if there are conflicts

## Git Status and Security Check
[ ] Check if there are new commits (use `git log {base_branch}..HEAD`). If no commits exist, development is incomplete - hand off to `develop`
[ ] Check for uncommitted changes (if any, development is incomplete)
[ ] Check for sensitive info in committed files (passwords, API keys, credentials)
[ ] If sensitive info found: treat as critical issue, require immediate removal from commit history

## Commit Message Style Check (Critical - Must Match Base Branch)
[ ] Get current branch commits: `git log {base_branch}..HEAD --pretty=format:"%H%n%B"`
[ ] Get base branch reference commits: `git log {base_branch} --max-count=5`
[ ] Determine base branch commit style: single-line or multi-line (subject + body lines, use `git log <sha> -1 --format="%B" | wc -l`)
[ ] Determine current branch commit style: same method
[ ] Check consistency: body presence (multi-line description) matches base branch
[ ] Check consistency: language (Chinese/English) matches base branch
[ ] If style mismatch found: list commit SHAs, explain correct style, provide update commands
[ ] Provide complete git rebase commands for developer to execute directly (non-interactive, see prompt)

## Implementation Completeness Check
[ ] Check for unfinished items in implementation plan
[ ] Compare implementation against {spec_file_path}
[ ] Verify all acceptance criteria are met
[ ] Confirm: Verified all requirements are met, nothing missed

## Code Quality Review
[ ] Check conformance to existing project coding style
[ ] Check if existing code patterns and utilities were reused
[ ] Check for code duplication or excessive duplicate code
[ ] Verify proper error handling
[ ] Check code correctness, readability, performance, security
[ ] Check for missing updates (error messages, prompts, documentation, examples)
[ ] Check for files that should not be committed (config files, log files)
[ ] Check for files or code that should not be deleted
[ ] Check if existing unused code can be removed

## Testing Review
[ ] Review test quality and edge cases
[ ] Check the tests are not fragile or flaky

## Final Steps
[ ] Confirm: No code was modified
[ ] Write review findings to {review_file_path} in todo list format (same format as PR phase)
[ ] Use this structure: ## Todo List / ### [Category] / - [ ] item or - [x] item
[ ] Group issues by category (e.g., "Commit Message Style", "Code Quality", "Testing")
[ ] Each issue should be a checkbox item with file path and line number
[ ] If no issues found, all items should be marked [x]
[ ] Do NOT provide code solutions, only identify issues
[ ] Update blackboard and next-step baton to hand off to the next workflow target
[ ] Keep the response brief; workflow transitions are controlled by the baton
