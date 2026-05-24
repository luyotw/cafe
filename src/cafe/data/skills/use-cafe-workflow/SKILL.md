---
name: use-cafe-workflow
description: Use this skill when you need to develop an issue by driving CAFE from the terminal with non-interactive commands instead of manually performing each phase.
version: 1.3.0
---

# Use CAFE Workflow

## Purpose
- Let CAFE run the spec, plan, develop, review, and PR phases through `cafe make`.
- Prefer non-interactive commands so the workflow can run unattended and resume cleanly.
- Treat CAFE artifacts, blackboard state, and baton handoffs as the source of workflow progress.
- Ground Q&A and PR review in **`.cafe/strategic_context.yaml`**—the single file for strategic documents, decision authority, and per-issue overrides. If referenced documents do not exist yet, **help the user create them before** `cafe make`.

## Strategic Context (one file: `.cafe/strategic_context.yaml`)

All higher-scope material lives in **one** project-root file. It answers:
1. **Which strategic documents exist** (roadmap, positioning, department norms, …) and their paths.
2. **How much the agent may decide** on each concern (axes + levels)—default for the repo, with optional per-issue overrides.

Do not split this into `mandate.yaml` or other parallel config files.

### Document categories (agree paths with the user)

| Category | What it answers | Example paths |
| --- | --- | --- |
| Product direction | What we are building, priorities, boundaries | `docs/roadmap.md` |
| Company positioning | Who we serve, positioning, non-goals | `docs/positioning.md` |
| Department / function norms | How a team operates | `CONTRIBUTING.md`, `docs/guidelines/*.md` |
| Playbook-specific policy | Rules for this workflow type | `docs/policies/<name>.md` |

**Gate:** If a needed category is `missing`, **do not start `cafe make`**. Interview the user, draft the document, get confirmation, save at the agreed path, set `status: exists` (or user-approved `draft`) in `strategic_context.yaml`, then continue.

### Kickoff (required before first `cafe make`)

1. Inventory existing docs; co-create any that are `missing`.
2. Confirm with the user: active playbook, **preset** (`issue-scoped` | `product-led` | `technical-led` | `full-stack` | `custom`), **axes** for that playbook (examples only—user may rename/add), **level** per axis (`agent` | `propose` | `escalate`), and **out_of_mandate** (billing, legal, production access, …).
3. Write everything to `.cafe/strategic_context.yaml`. For this issue only, add an entry under `issues.<issue-name>` when it differs from the repo default.

**Levels:** `agent` = decide within strategic docs + issue artifacts; `propose` = recommend then continue per playbook; `escalate` = must ask the user.

### Schema (single file)

```yaml
version: 1

documents:
  roadmap:
    path: docs/roadmap.md
    status: exists          # exists | draft | missing
  positioning:
    path: docs/positioning.md
    status: missing
  engineering_guidelines:
    path: CONTRIBUTING.md
    status: exists

mandate:
  preset: technical-led
  playbook_id: default
  axes:
    product_scope:
      level: escalate
      grounds: [roadmap, positioning]
    technical:
      level: agent
      grounds: [engineering_guidelines]
    quality:
      level: agent
  out_of_mandate:
    - pricing
    - production deploy approval
  notes: |
    Default for this repo. User confirmed 2026-05-23.

issues:
  issue301:
    playbook_id: default
    axes:
      product_scope: { level: escalate }
      technical: { level: agent }
    notes: |
      This issue only: stay within v0.2 roadmap scope.
```

- **`documents`** — strategic layer; agent reads these paths for direction.
- **`mandate`** — repo-wide default authority.
- **`issues.<name>`** — optional; only fields that differ from `mandate`. Omit when the default applies.

Re-read `.cafe/strategic_context.yaml` and linked documents before answering questions, reviewing PRs, or merging.

### Apply

**Answering questions:** Resolve `issues.<current-issue>` over `mandate` over documents. Classify by axis → level → strategic docs + issue spec/plan. Contradicting or extending a strategic document = escalate. `missing` document = go back to co-creation, do not invent strategy.

**PR review:** Blocking findings only for in-mandate axes backed by `exists`/`draft` documents. Merge/close/`cafe close` only when those blockers are resolved.

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
6. **Strategic Context:** inventory, co-create missing documents, confirm mandate with user, write `.cafe/strategic_context.yaml` (including `issues.<issue-name>` if needed), then run the first `cafe make`.

## Running Work
1. Start the workflow with the user's requirement. Point agents at the single config when useful:
   ```bash
   cafe make --user-input "<requirement or answer>. Strategic context: .cafe/strategic_context.yaml (issue: <issue-name>)"
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
- If PR auto-create is enabled, verify the PR URL printed by CAFE (or `gh pr view` on the feature branch).
- If PR auto-create is disabled, inspect the local PR artifact with `cafe show pr output` and open the PR before shipping.

### PR review and ship
After the PR phase completes, do not stop at the handoff:
1. Re-read `.cafe/strategic_context.yaml` (resolve `issues.<current-issue>` if present) and linked documents. Review the PR within that scope. Apply required fixes through CAFE (`cafe make`, focused commits) or direct edits, push the branch, and repeat until there are **no blocking findings within mandate**.
2. When review is clean, **merge and close without waiting for a separate human approval**:
   ```bash
   PR=$(gh pr view --json number -q .number)
   gh pr merge "$PR" --merge
   ```
   Use `--squash` instead of `--merge` only when the target repo's convention requires it.
3. Close the linked GitHub issue when `issue.yaml` has `spec.issue_id`:
   ```bash
   gh issue close <issue-id> --comment "Merged via PR #${PR}."
   ```
4. Tear down the CAFE issue locally. Run `cafe close` from the issue worktree if `worktree_path` is set; otherwise run it from the main repo while on the feature branch. (`cafe close` blocks while the PR is still open; merge first.)

### Before reporting done
- Run the relevant tests or confirm which CAFE test plan already ran.
- Confirm the issue no longer appears in `cafe ls` (data archived under `~/.cafe/projects/<project>/archived/`).
