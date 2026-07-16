---
name: use-cafe-workflow
description: Use this skill when you need to develop an issue by driving CAFE from the terminal with non-interactive commands instead of manually performing each phase.
version: 1.5.1
---

# Use CAFE Workflow

## Purpose
- Let CAFE run the spec, plan, develop, review, and PR phases through `cafe make`.
- Prefer non-interactive commands so the workflow can run unattended and resume cleanly.
- Treat CAFE artifacts, blackboard state, and baton handoffs as the source of workflow progress.
- Do not start a CAFE workflow until the user has confirmed the kickoff
  contract: playbook, driver authority, user handoff stops, and worktree
  behavior.
- Ground Q&A and PR review in **`.cafe/strategic_context.yaml`**—the single file for strategic documents, decision authority, and user-authorized per-issue overrides. If referenced documents do not exist yet, **help the user create them before** `cafe make`.

## Kickoff Stop Contract (first blocking gate)

Before `cafe prepare`, any repository mutation, or the first `cafe make`, agree
with the user which planned confirmation gates must stop for them and how
reactive user handoffs will be handled. Do not reuse a repo default or another
issue's contract silently. When resuming the same issue, validate and honor its
confirmed issue contract; reconfirm only if it is missing, invalid, or stale. If
the user already made the choice in the current request, restate it for
confirmation instead of asking again.

### Derive candidates from the active playbook

1. Resolve the active playbook from the user's request or `.cafe/config.yaml`.
2. Run:
   ```bash
   cafe playbook confirmation-gates <playbook-id>
   ```
3. Treat exactly the reported steps as candidates. The command derives them
   from `steps.<step>."on".confirm_output`, the playbook declaration for a
   planned output-confirmation baton to the user.
4. Present the candidates by step name and purpose, recommend that every
   candidate stop for the user, and ask the user to assign each one to exactly
   one of:
   - `user_required`: the driver must stop for the real user;
   - `driver_confirmable`: the driver may verify the output and continue.
5. Do not continue until the user explicitly accepts the assignment. If there
   are no candidates, still confirm that the workflow has no scheduled
   confirmation stops.

`need_clarification`, `need_permission`, and `alignment_checkpoint` are reactive
safety interruptions. They may still baton to the user when triggered, but they
are not scheduled kickoff candidates. Still name the reactive policy in the
kickoff contract:
- `need_clarification` stops for the real user unless the exact answer has
  already been supplied in the current thread.
- `need_permission` stops for the real user unless the exact permission has
  already been supplied in the current thread.
- `alignment_checkpoint` stops for the real user unless the kickoff contract
  explicitly authorizes the driver to resolve clear, in-mandate alignment
  decisions.

`manual_handoff` is routing, not a planned confirmation gate. Any runtime baton
with `to_owner=user`, or terminal output such as `Workflow is waiting for user
input`, that is not covered by the confirmed kickoff contract is a hard stop for
the driver.

The two contract lists must be disjoint and their union must equal the derived
candidate set. Reject unknown steps, missing candidates, overlaps, role names,
and steps that merely exist in the playbook without declaring
`on.confirm_output`. If the active playbook or its gate set changes, reconfirm
the contract before the next `cafe make`.

### Persist after prepare

Keep the agreed assignment in driver context until `cafe prepare` creates the
issue. Then persist it in the active issue's
`.cafe/issues/<issue-name>/issue.yaml` before the first `cafe make`:

```yaml
playbook_id: default
confirmation_contract:
  user_required: [spec, plan]
  driver_confirmable: []
  confirmed_by: user
  confirmed_at: 2026-07-16
reactive_user_handoffs:
  need_clarification: user_required
  need_permission: user_required
  alignment_checkpoint: driver_resolvable_when_clear
```

The values above are an example derived from the `default` playbook, not global
defaults. In worktree mode, write the contract to the issue file inside the
worktree, which is the runtime copy. A legacy
`mandate.confirmation_contract.agent_confirmable` value in
`.cafe/strategic_context.yaml` is only a kickoff proposal: rename it to
`driver_confirmable`, compare it with the active playbook, and get fresh user
confirmation before persisting the issue contract.

## Strategic Context (one file: `.cafe/strategic_context.yaml`)

All higher-scope material lives in **one** project-root file. It answers:
1. **Which strategic documents exist** (roadmap, positioning, department norms, …) and their paths.
2. **How much the agent may decide** on each concern (axes + levels)—default for the repo, with optional per-issue overrides only when explicitly requested by the user.

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
2. Before preparing the issue, confirm with the user: active playbook, the kickoff stop contract above, reactive user-handoff policy, **preset** (`issue-scoped` | `product-led` | `technical-led` | `full-stack` | `custom`), **axes** for that playbook (examples only—user may rename/add), **level** per axis (`agent` | `propose` | `escalate`), **out_of_mandate** (billing, legal, production access, …), and whether to create a Git worktree.
3. Recommend creating a worktree by default at `.cafe/worktrees/<issue-name>`. Include this recommendation in the kickoff confirmation; if the user confirms the recommended kickoff without changing the worktree choice, treat worktree creation as approved. If the user declines, prepare on a feature branch in the current checkout.
4. Write repo-wide `documents` and `mandate` updates to `.cafe/strategic_context.yaml`. Do **not** create, edit, or delete `issues.<issue-name>` unless the user explicitly asks for an issue-specific strategic override.

### Issue overrides are opt-in only

The `issues:` section is protected. Unless the user explicitly asks to add,
change, or remove an issue-level override, do not write to this section.

- Do not create `issues.<issue-name>` just because the current task looks
  narrower than the repo default.
- Do not store workflow progress, baton state, phase outputs, review notes, or
  temporary scope summaries in `issues:`.
- If an issue appears to need different authority than `mandate`, ask the user
  before writing the override; otherwise keep the repo-wide mandate and let the
  alignment gate escalate normally.

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

# Optional and protected. Include only when the user explicitly asks for an
# issue-specific strategic override.
# issues:
#   issue301:
#     playbook_id: default
#     axes:
#       product_scope: { level: escalate }
#       technical: { level: agent }
#     notes: |
#       This issue only: stay within v0.2 roadmap scope.
```

- **`documents`** — strategic layer; agent reads these paths for direction.
- **`mandate`** — repo-wide default authority.
- **`issues.<name>`** — optional and protected. Only write it when the user explicitly requests an issue-specific strategic override; otherwise omit it even if the current issue seems narrower than the repo default.

Re-read `.cafe/strategic_context.yaml` and linked documents before answering questions, reviewing PRs, or merging.

### Apply

**Answering questions:** Resolve `issues.<current-issue>` over `mandate` over documents. Classify by axis → level → strategic docs + issue spec/plan. Contradicting or extending a strategic document = escalate. `missing` document = go back to co-creation, do not invent strategy.

**PR review:** Blocking findings only for in-mandate axes backed by `exists`/`draft` documents. Merge/close/`cafe close` only when those blockers are resolved.

## Initial Setup
1. Resolve the active playbook, derive its confirmation gates and reactive
   user-handoff policy, and complete the kickoff stop contract as the first
   blocking interaction with the user.
2. Check the repo state with `git status --short --branch`.
3. If CAFE is not initialized, run `cafe init --preset <preset>` instead of interactive `cafe init`.
4. Complete the rest of kickoff confirmation, including the worktree choice, before running `cafe prepare`.
5. Prepare the issue non-interactively. Worktree mode is the default:
   ```bash
   cafe prepare <issue-name> --no-interactive --input-method=manual --rigor=medium --spec-template=auto --plan-template=default --worktree .cafe/worktrees/<issue-name>
   ```
6. For a GitHub-backed issue, use:
   ```bash
   cafe prepare <issue-name> --no-interactive --input-method=github --issue-id=<number> --rigor=medium --spec-template=auto --plan-template=default --auto-create-pr --worktree .cafe/worktrees/<issue-name>
   ```
7. If the user declined worktree mode, omit `--worktree`; otherwise do not silently fall back to the main checkout when worktree creation fails.
8. If the prepare command creates or reports a worktree, `cd` into that worktree before running workflow commands.
9. Persist the agreed stop contract and active `playbook_id` in the issue file of the active checkout. Re-run `cafe playbook confirmation-gates <playbook-id>` and verify the two lists form an exact partition before continuing.
10. If the issue was accidentally prepared without the confirmed worktree before its first `cafe make`, recreate or repair the preparation so `issue.yaml` records `worktree_path`, then continue from the worktree without discarding issue configuration.
11. **Strategic Context:** inventory, co-create missing documents, confirm mandate with user, write repo-wide `.cafe/strategic_context.yaml` updates, and leave `issues:` untouched unless the user explicitly requested an issue-specific strategic override. Then run the first `cafe make`.

## Running Work
1. Start the workflow with the user's requirement. Point agents at the single config when useful:
   ```bash
   cafe make --user-input "<requirement or answer>. Strategic context: .cafe/strategic_context.yaml (issue: <issue-name>)"
   ```
2. Resume later with:
   ```bash
   cafe make
   ```
3. If the workflow is paused for user input, apply the stop contract before
   resuming. Resume non-interactively only when the exact answer/permission was
   already supplied by the user in the current thread, or the pause is allowed
   by the confirmed issue contract and passes the verification rules below:
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

## Applying The Stop Contract

The kickoff contract controls who may answer any user-owned pause. It is
separate from the mandate axes: mandate says what the driver may decide, while
the stop contract says who must approve output at a planned gate or answer a
reactive user handoff. The contract is driver policy and is not parsed or
auto-approved by CAFE runtime.

When CAFE pauses for the user, including `to_owner=user`,
`intent=confirm_output`, `intent=need_clarification`,
`intent=need_permission`, `intent=alignment_checkpoint`, or terminal output
such as `Workflow is waiting for user input`:

1. Identify `from_step`, `to_owner`, and `intent` from the blackboard handoff
   contract, latest `next_step.txt`, or terminal output.
2. Read `confirmation_contract` and `reactive_user_handoffs` from the active
   issue's `issue.yaml`; verify `confirmation_contract.playbook_id` and its
   exact candidate partition against
   `cafe playbook confirmation-gates <playbook-id>`.
3. If the contract is missing, stale, invalid, or omits the current pause
   policy, stop for the user and repair the contract before continuing.
4. For `confirm_output`: if `from_step` is in `user_required`, stop and ask the
   user to approve or request changes. If `from_step` is in
   `driver_confirmable`, read the latest step output and required input
   artifacts. Confirm only when the output is complete, in-mandate, and
   consistent with the confirmed upstream artifacts.
5. For `need_clarification`: stop unless the exact answer has already been
   supplied by the user in the current thread. Do not infer an answer merely
   from strategic docs.
6. For `need_permission`: stop unless the exact permission has already been
   supplied by the user in the current thread. Never grant production access,
   destructive actions, or external side effects on the user's behalf.
7. For `alignment_checkpoint`: apply the saved reactive policy. Continue only
   when the policy explicitly allows driver resolution and the decision follows
   directly from confirmed strategic docs, issue acceptance criteria, and
   existing mandate. Otherwise stop for the user.
8. For any other `to_owner=user` pause, stop for the real user. Unknown user
   handoffs are not driver-confirmable by default.

Driver-confirmable does not mean the phase agent approves itself. It means the
workflow driver may resume non-interactively after verification, for example:

```bash
cafe make --user-input "confirmed"
```

Use a correction instead of `confirmed` when the output is close but needs a
bounded revision that follows directly from confirmed context. Stop for the user
when approval would change requirements, implementation direction, public
positioning, business/legal/pricing decisions, production access, destructive
operations, or any ambiguous tradeoff.

## Alignment Checkpoints

When CAFE pauses with `intent=alignment_checkpoint`, apply the stop contract
first. The workflow driver may resolve the checkpoint only when the saved
reactive policy allows it and the decision is clear from confirmed project
context. Do not automatically hand off every checkpoint to the user, but do not
bypass the kickoff contract either.

### Inspect

1. Read the latest `.cafe/issues/<issue>/<step>/iteration_*/alignment_request.json`.
2. Re-read `.cafe/strategic_context.yaml` and every referenced strategic document
   that is relevant to the request.
3. Read the current issue artifacts needed to understand the proposed scope
   (`spec`, `plan`, or the blocked step output).
4. Classify the checkpoint against the repo mandate:
   - **Resolvable by driver:** the decision follows directly from confirmed
     strategic docs, issue acceptance criteria, and existing mandate.
   - **Needs user:** the decision changes or confirms product positioning,
     roadmap direction, principles, trusted capability boundaries beyond existing
     docs, business/legal/pricing/production access, or any ambiguous tradeoff.

### Driver-owned Decisions

If the checkpoint is resolvable by the driver, continue non-interactively with an
explicit JSON decision payload. Plain text must not be used for alignment approval.

Examples:

```bash
cafe make --user-input '{"decision":"approve","reason":"Within confirmed roadmap and capability boundary."}'
```

```bash
cafe make --user-input '{"decision":"narrow_scope","correction":"Keep this to PR publish plumbing; do not introduce a broader product-level contract model."}'
```

```bash
cafe make --user-input '{"decision":"revise_spec","correction":"Specify that capability contracts protect trusted host execution boundaries; broad product ontology is out of scope."}'
```

Use `approve` only when no missing or draft strategic document blocks the
decision. Use `narrow_scope`, `revise_spec`, or `revise_plan` when the desired
alignment correction is clear from confirmed context.

### Strategic Document Updates

If a strategic document is `missing` or `draft`, the driver may draft or revise
the document, but must not treat its own draft as confirmed strategy.

The driver may mark a document `status: exists` and continue with
`strategic_documents_updated` only when one of these is true:

- the user explicitly confirmed the final document content in the current
  thread/chat; or
- the document content is copied or mechanically split from an already confirmed
  strategic document, with no new product judgment.

When finalizing confirmed strategic documents non-interactively, include
confirmation evidence in the JSON payload:

```bash
cafe make --user-input '{"decision":"strategic_documents_updated","reason":"Positioning doc confirmed and strategic_context updated.","user_confirmed":true,"user_confirmation":"User confirmed the positioning framing: primary trusted host capability boundary, secondary external mutation risk; broad product contract model is out of scope."}'
```

If the document requires product judgment and the user has not confirmed it,
leave the document as `draft` or `missing` and ask the user concise questions.
Do not write `strategic_documents_updated`.

### Ask The User When Uncertain

When the driver cannot resolve the checkpoint confidently, stop and ask the user
one focused question with a recommended answer and tradeoff. Good questions name
the decision axis directly, for example:

```text
For #347, should capability contracts be positioned primarily as:
1. trusted host capability boundary protection (recommended),
2. external mutation risk reduction, or
3. a broader product-level contract model?
```

After the user answers, apply the answer through the same JSON decision flow
instead of opening the interactive menu unless the user asks for chat.

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
- Prefer `cafe make` over legacy per-step commands (removed in issue #315). For a single step use `cafe workflow --start-step <step> --execute`.
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
