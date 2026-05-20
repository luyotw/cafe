---
name: use-cafe-workflow
description: Use this skill when you need to develop an issue by driving CAFE from the terminal with non-interactive commands instead of manually performing each phase.
version: 1.0.0
---

# Use CAFE Workflow

## Purpose
- Let CAFE run the spec, plan, develop, review, and PR phases through `cafe make`.
- Prefer non-interactive commands so the workflow can run unattended and resume cleanly.
- Treat CAFE artifacts, blackboard state, and baton handoffs as the source of workflow progress.

## Initial Setup
1. Check the repo state with `git status --short --branch`.
2. If CAFE is not initialized, run `cafe init --preset <preset>` instead of interactive `cafe init`.
3. Prepare the issue non-interactively:
   ```bash
   cafe prepare <issue-name> --no-interactive --input-method=manual --rigor=medium --spec-template=auto --plan-template=default
   ```
4. For a GitHub-backed issue, use:
   ```bash
   cafe prepare <issue-name> --no-interactive --input-method=github --issue-id=<number> --rigor=medium --spec-template=auto --plan-template=default --auto-create-pr
   ```
5. If the prepare command creates or reports a worktree, `cd` into that worktree before running workflow commands.

## Running Work
1. Start the workflow with the user's requirement:
   ```bash
   cafe make --user-input "<requirement or answer>"
   ```
2. Resume later with:
   ```bash
   cafe make
   ```
3. If the workflow is paused for user input and the answer is known, resume non-interactively:
   ```bash
   cafe make --user-input "<answer>"
   ```
4. If a specific step must be retried, use the generic workflow command:
   ```bash
   cafe workflow --execute --start-step <step>
   ```
5. For one-step diagnosis, add `--single-step`:
   ```bash
   cafe workflow --execute --start-step <step> --single-step
   ```

## Useful Options
- Use `--fallback-preset <preset>` when the primary CLI is rate-limited, unavailable, missing, or configured with a bad model.
- Use repeated `--add-dir <path>` for extra directories the agents must read or edit.
- Prefer configuring stable extra directories in `.cafe/config.yaml` as `allowed_directories`.
- Keep `--add-dir` values relative to the current worktree and make sure the directories exist before running CAFE.

## Inspecting Progress
- Use `cafe summary` for the phase timeline.
- Use `cafe show <step> output` to inspect the latest step result.
- Use `cafe show <step> questions` when the workflow is waiting for clarification.
- Use `cafe show <step> checklist` to see what the agent still must complete.
- Read `.cafe/issues/<issue>/blackboard.json` only when command output is insufficient to understand the current handoff.

## Operating Rules
- Prefer `cafe make` over manually running `cafe spec`, `cafe plan`, `cafe develop`, `cafe review`, or `cafe pr`.
- Do not manually edit workflow artifacts, blackboard, or `next_step.txt` unless you are repairing a broken workflow state.
- Do not bypass CAFE by directly asking an agent to implement the issue when the user asked to use the CAFE workflow.
- If CAFE reports uncommitted chat handoff changes, commit or stash the relevant changes before resuming.
- If CAFE reports a baton contract error, rerun the responsible step with `cafe workflow --execute --start-step <step>` so the agent can rewrite the baton.
- If PR sync fails because the branch has uncommitted changes, commit or stash them, then rerun `cafe make`.

## Completion
- The normal terminal state is `Workflow completed ... next=done`.
- If PR auto-create is enabled, verify the PR URL printed by CAFE.
- After the PR is created and the PR phase hands off to the user, do not stop at the handoff. Review the PR yourself with the repo's PR review workflow, apply any required fixes through CAFE or focused commits, push the branch, and repeat review until the PR is approved or there are no blocking findings.
- If PR auto-create is disabled, inspect the local PR artifact with `cafe show pr output`.
- Before reporting completion, run the relevant tests or confirm which CAFE test plan already ran.
