---
name: use-cafe-workflow
description: Use this skill when you need to develop an issue by driving CAFE from the terminal with non-interactive commands, including bounded diagnosis and declarative repair when the workflow behaves incorrectly.
version: 1.10.3
---

# Use CAFE Workflow

## Purpose
- Let CAFE run the spec, plan, develop, review, and PR phases through `cafe make`.
- Prefer non-interactive commands so the workflow can run unattended and resume cleanly.
- Treat CAFE artifacts, blackboard state, and baton handoffs as the source of workflow progress.
- Do not start a CAFE workflow until the user has confirmed the complete
  kickoff contract: playbook, conversation locale, driver authority, user
  handoff stops, and worktree behavior.
- Resolve the active playbook's conversation locale before the kickoff and use
  it for every driver-to-user message.
- Keep semantic alignment decisions in the workflow driver. Bundled playbooks
  omit alignment configuration, so the globally registered compatibility hook
  remains inactive for them.
- Ground Q&A and PR review in **`.cafe/strategic_context.yaml`**—the single file for strategic documents, decision authority, and user-authorized per-issue overrides. If referenced documents do not exist yet, **help the user create them before** `cafe make`.

## Conversation Locale

The active playbook's `playbook.conversation_locale` is the source of truth for the
workflow driver's conversation with the user. Resolve it before presenting the
kickoff contract or asking the first workflow question:

1. Resolve the active playbook from the user's request or `.cafe/config.yaml`.
2. Run `cafe playbook confirmation-gates <playbook-id>` and read its
   `Conversation locale:` line together with the confirmation-gate candidates.
3. Resolve the effective conversation locale. A BCP 47 language tag such as
   `zh-TW` or `en-US` is already effective. For `auto`, use the language of the
   user's current request.
4. Include the effective value and source in the kickoff confirmation message,
   for example: `conversation_locale: en-US (from playbook: default)`. This is
   a required kickoff field, not a confirmation gate.
5. Apply the effective locale to kickoff, clarification and permission
   questions, alignment checkpoints, progress/error reports, and completion
   messages. Keep commands, paths, playbook/step names, intents, artifact keys,
   payload fields, and quoted source text unchanged.
6. A direct language instruction from the user, such as "use Traditional
   Chinese for this thread", may override the configured locale for that
   thread. Merely writing in another language or asking why a language was used
   is not an override. Do not mutate the playbook merely to record a
   conversational override.
7. If the user asks about the language choice, report the configured value,
   effective value, and source. Never claim that this skill lacks a conversation
   locale rule.

Do not copy the conversation locale into `issue.yaml`; re-resolve it from the
active playbook when starting or resuming a workflow and whenever the playbook
changes.

## Kickoff Contract (first blocking gate)

Before `cafe prepare`, any repository mutation, or the first `cafe make`,
confirm the complete kickoff contract with the user. Do not reuse a repo default
or another issue's contract silently. When resuming the same issue, validate and
honor its confirmed issue contract; reconfirm only if it is missing, invalid, or
stale.

The confirmation message must include every required field:
- `playbook_id`
- `conversation_locale` with source
- planned confirmation gates split into `user_required` and
  `driver_confirmable`
- `reactive_user_handoffs`
- mandate preset, axes, levels, and out-of-mandate list
- worktree choice and path, when using a worktree

Render the complete proposal with the bundled deterministic formatter before
presenting it. Resolve the script path relative to this `SKILL.md` and run:

```bash
python3 <skill-dir>/scripts/format_kickoff_contract.py <playbook-id> \
  --issue-name <issue-name> \
  --effective-locale <locale> \
  --locale-source "<playbook or direct-user-override source>" \
  --user-required <steps...> \
  --driver-confirmable <steps...> \
  --worktree .cafe/worktrees/<issue-name>
```

Pass an option with no step values to represent an explicit empty list. The
formatter validates that `user_required` and `driver_confirmable` are disjoint
and exactly partition the playbook's `confirm_output` candidates. Its Markdown
output includes every playbook phase, role, skill, scheduled gate, planned
owner, whether execution will stop for the user, reactive handoff policies,
mandate axes, out-of-mandate boundaries, locale source, and worktree choice.
Present this table as the kickoff contract; do not replace it with a prose-only
summary. The formatter automatically re-executes with the Python interpreter
that owns the installed `cafe` command when the shell's `python3` lacks CAFE's
runtime dependencies.

If the user already made a choice in the current request, restate it for
confirmation instead of asking again.

### Derive candidates from the active playbook

1. Resolve the active playbook and its conversation locale as described above.
2. Run:
   ```bash
   cafe playbook confirmation-gates <playbook-id>
   ```
3. Treat exactly the reported steps as confirmation-gate candidates. The command
   derives them from `steps.<step>."on".confirm_output`, the playbook
   declaration for a planned output-confirmation baton to the user.
4. Present the effective locale and the candidates by step name and purpose,
   recommend that every candidate stop for the user, and ask the user to assign
   each candidate to exactly one of:
   - `user_required`: the driver must stop for the real user;
   - `driver_confirmable`: the driver may verify the output and continue.
5. Do not continue until the user explicitly accepts the complete kickoff
   contract. If there are no candidates, still confirm that the workflow has no
   scheduled confirmation stops.

`need_clarification` and `need_permission` are reactive safety interruptions.
They may still baton to the user when triggered, but they are not scheduled
kickoff candidates. Alignment is a proactive driver decision, not a scheduled
playbook gate. Its authority comes from the confirmed mandate rather than a
second issue-level policy. Still name the reactive policy in the kickoff
contract:
- `need_clarification` stops for the real user unless the exact answer has
  already been supplied in the current thread.
- `need_permission` stops for the real user unless the exact permission has
  already been supplied in the current thread.
- The driver proceeds without asking only when the proposal is clearly within
  confirmed strategic documents and the resolved mandate level is `agent`
  (or `propose` explicitly permits continuing after a grounded recommendation).
- The driver stops when the proposal contradicts or extends confirmed strategy,
  needs a strategic choice, or cannot be classified confidently.
- A legacy or custom `alignment_checkpoint` handoff is compatibility input to
  the same driver decision; it is not proof that user alignment is required.

`manual_handoff` is routing, not a planned confirmation gate. Any runtime baton
with `to_owner=user`, or terminal output such as `Workflow is waiting for user
input`, that is not covered by the confirmed kickoff contract is a hard stop for
the driver.

The two confirmation-gate lists must be disjoint and their union must equal the
derived candidate set. Reject unknown steps, missing candidates, overlaps, role
names, and steps that merely exist in the playbook without declaring
`on.confirm_output`. The confirmed kickoff contract must name a concrete
effective locale such as `zh-TW`, not an unstated assumption. If the active
playbook, effective locale, or gate set changes, reconfirm the contract before
the next `cafe make`.

### Persist after prepare

Keep the agreed kickoff contract in driver context until `cafe prepare` creates
the issue. Then persist the issue-owned parts in the active issue's
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
2. Before preparing the issue, confirm with the user: active playbook,
   conversation locale, the kickoff contract above, reactive user-handoff
   policy, **preset** (`issue-scoped` | `product-led` | `technical-led` |
   `full-stack` | `custom`), **axes** for that playbook (examples only—user may
   rename/add), **level** per axis (`agent` | `propose` | `escalate`),
   **out_of_mandate** (billing, legal, production access, …), and whether to
   create a Git worktree.
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
  driver classify any strategic delta.

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
1. Resolve the active playbook, derive its effective conversation locale,
   confirmation gates and reactive user-handoff policy, then complete the
   kickoff contract as the first blocking interaction with the user.
2. Check the repo state with `git status --short --branch`.
3. If CAFE is not initialized, run `cafe init --preset <preset>` instead of interactive `cafe init`.
4. Complete the rest of kickoff confirmation, including the worktree choice, before running `cafe prepare`.
5. Prepare the issue non-interactively. Worktree mode is the default:
   ```bash
   cafe prepare <issue-name> --no-interactive --input-method=manual --rigor=medium --spec-template=auto --plan-template=default --worktree .cafe/worktrees/<issue-name>
   ```
6. For a GitHub-backed issue, use:
   ```bash
   cafe prepare <issue-name> --no-interactive --input-method=github --issue-id=<number> --rigor=medium --spec-template=auto --plan-template=default --worktree .cafe/worktrees/<issue-name>
   ```
7. If the user declined worktree mode, omit `--worktree`; otherwise do not silently fall back to the main checkout when worktree creation fails.
8. If the prepare command creates or reports a worktree, `cd` into that worktree before running workflow commands.
9. Persist the agreed issue-owned kickoff contract and active `playbook_id` in
   the issue file of the active checkout. Re-run
   `cafe playbook confirmation-gates <playbook-id>` and verify the effective
   locale and two confirmation-gate lists before continuing.
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
3. If the workflow is paused for user input, apply the user-handoff rules from
   the kickoff contract before resuming. Resume non-interactively only when the
   exact answer/permission was already supplied by the user in the current
   thread, or the pause is allowed by the confirmed issue contract and passes
   the verification rules below:
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

## Applying User-Handoff Rules

The kickoff contract controls who may answer any user-owned pause. It is
separate from the mandate axes: mandate says what the driver may decide, while
the kickoff contract says who must approve output at a planned gate or answer a
reactive user handoff. This policy is driver-side and is not parsed or
auto-approved by CAFE runtime.

When CAFE pauses for the user, including `to_owner=user`,
`intent=confirm_output`, `intent=need_clarification`,
`intent=need_permission`, `intent=alignment_checkpoint`, or terminal output
such as `Workflow is waiting for user input`:

1. Identify `from_step`, `to_owner`, and `intent` from the blackboard handoff
   contract, latest `next_step.txt`, or terminal output.
2. Re-resolve the effective conversation locale. Read `playbook_id`,
   `confirmation_contract`, and `reactive_user_handoffs` from the active issue's
   `issue.yaml`; verify the exact candidate partition against
   `cafe playbook confirmation-gates <playbook-id>`.
3. If the contract is missing, stale, invalid, the effective locale cannot be
   resolved, or the contract omits the current pause policy, stop for the user
   and repair the contract before continuing.
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
7. For a legacy or custom `alignment_checkpoint`, apply the driver-owned
   alignment classification below. The core checkpoint is evidence to inspect,
   not an automatic reason to ask the user.
8. For any other `to_owner=user` pause, stop for the real user. Unknown user
   handoffs are not driver-confirmable by default.

Driver-confirmable does not mean the phase agent approves itself. It means the
workflow driver may resume non-interactively after verification, for example:

```bash
cafe make --user-input "confirmed"
```

Use a correction instead of `confirmed` when the output is close but needs a
bounded revision that follows directly from confirmed context. Stop for the user
when approval would change confirmed requirements beyond driver authority,
public positioning, business/legal/pricing decisions, production access,
destructive operations, or any ambiguous strategic tradeoff.

## Driver-Owned Alignment

The workflow driver owns the final semantic alignment decision. Bundled
playbooks omit `alignment:` configuration, so the globally registered
`AlignmentCheckpointGate` is inactive. An explicitly opted-in custom playbook
may still use the core heuristic to propose a compatibility checkpoint, but the
driver must not treat that proposal as the final judgment.

Alignment answers one question: **does the newest proposed scope remain within
confirmed strategic documents and the user's mandate?** It is not normal spec
confirmation, implementation clarification, or permission for an external
side effect.

### Evaluation Boundaries

Evaluate alignment:

1. During kickoff, after reading `.cafe/strategic_context.yaml` and the relevant
   strategic documents, before the first `cafe make`.
2. Before driver-confirming a spec or plan output.
3. When a correction delta changes requirements, product scope, positioning,
   principles, mandate, or trusted capability boundaries.

Do not re-evaluate an unchanged scope merely because the workflow moved to
develop, review, or PR. A correction that only fixes implementation or review
findings inherits the latest accepted alignment result.

### Evidence And Classification

Use the newest user request or correction delta, the latest accepted spec, and
only the relevant strategic documents. Do not classify from incidental keyword
mentions, negative-space statements, generated boilerplate, or the full history
of phase artifacts.

Write down this evidence tuple in driver reasoning before deciding:

- `proposal_delta`: the concrete new or changed scope
- `strategic_ground`: the exact document section, mandate axis, or
  out-of-mandate item that governs it
- `mandate_level`: the resolved issue override or repo mandate level for that
  axis (`agent`, `propose`, or `escalate`)
- `relation`: `within`, `contradicts`, `extends`, `missing_ground`, or
  `uncertain`

Then act:

- `within` + `agent`: continue without asking the user.
- `within` + `propose`: state the grounded recommendation and continue as
  allowed by the playbook.
- `within` + `escalate`: stop for the user; the confirmed mandate reserves that
  axis even when the proposal is compatible with existing documents.
- `contradicts` or `extends`: stop and ask the user one focused alignment
  question.
- `missing_ground` or `uncertain`: stop; do not invent strategy or silently
  narrow the request.

Except for an explicit `escalate` mandate, only a concrete proposal delta plus
a strategic ground may cause an alignment stop. A score assembled from several
weak signals is not sufficient.

Treat clarification and permission separately:

- Missing product or implementation facts use `need_clarification`.
- Production access, destructive operations, credentials, and external side
  effects use `need_permission`.
- A clear in-roadmap implementation choice needs neither.

### Asking And Resuming

When alignment is required, ask one focused question that names the governing
axis, the proposed delta, and a recommended option with its tradeoff. After the
user answers, pass the answer to the responsible spec or plan step with
`cafe make --user-input "<answer>"` so the accepted artifact records the
decision. Update a strategic document only when the user explicitly confirms
the new strategic content.

If a strategic document is `missing` or `draft`, the driver may draft it but
must not treat its own draft as confirmed strategy. Leave it `draft` or
`missing` until user confirmation unless the change is a mechanical copy or
split from already confirmed material.

### Legacy Or Custom Core Checkpoints

A legacy or explicitly opted-in custom playbook may still pause with
`intent=alignment_checkpoint`. Treat its request as compatibility evidence, not
as proof that the user must decide:

1. Read the latest
   `.cafe/issues/<issue>/<step>/iteration_*/alignment_request.json`.
2. Apply the same evidence tuple and classification above.
3. If `within` and `mandate_level` is `agent`, resume with an explicit JSON
   decision payload; plain text must not approve a core checkpoint:

   ```bash
   cafe make --user-input '{"decision":"approve","reason":"Within confirmed roadmap and mandate."}'
   ```

4. If `within` and `mandate_level` is `propose`, follow the playbook's grounded
   recommendation flow.
5. If `mandate_level` is `escalate`, or the relation is `contradicts`,
   `extends`, `missing_ground`, or `uncertain`, stop for the user.

Use `narrow_scope`, `revise_spec`, or `revise_plan` only when the correction
follows directly from confirmed context. `strategic_documents_updated` requires
explicit user confirmation evidence unless the update is mechanically copied
from confirmed strategic material.

## Useful Options
- Use `--fallback-preset <preset>` when the primary CLI is rate-limited, unavailable, missing, or configured with a bad model.
- Use repeated `--add-dir <path>` for extra directories the agents must read or edit.
- Prefer configuring stable extra directories in `.cafe/config.yaml` as `allowed_directories`.
- Keep `--add-dir` values relative to the current worktree and make sure the directories exist before running CAFE.

## Inspecting Progress
- Use `cafe status` for the phase timeline.
- Use `cafe show <step> output` to inspect the latest step result.
- Use `cafe show <step> questions` when the workflow is waiting for clarification.
- Use `cafe show <step> checklist` to see what the agent still must complete.
- Read `.cafe/issues/<issue>/blackboard.json` only when command output is insufficient to understand the current handoff.

## Bounded Self-Diagnosis And Declarative Repair

When workflow behavior looks wrong, diagnose only far enough to classify the
failure and choose a safe disposition. Keep the investigation bounded to the
failing command, active playbook and step, supplied artifacts, blackboard and
baton state, relevant sanitized logs, and the installed CAFE version. Do not
turn a workflow incident into open-ended framework refactoring.

### Classify before editing

1. Reproduce the failure with read-only inspection or a focused
   `--single-step` run when retrying is safe.
2. Rule out project configuration errors, malformed project artifacts, stale
   installed skill copies, CLI/model mismatch, transient provider or network
   failures, rate limits, and an agent failing to follow an otherwise valid
   contract.
3. Choose one disposition:
   - **Playbook declarative defect:** a step graph, artifact binding, intent,
     hook/tool declaration, or planned confirmation gate is wrong.
   - **Phase declarative defect:** a phase/shared/chat skill contract,
     placeholder, routing rule, or supporting skill resource is wrong.
   - **Driver or CAFE core defect:** `use-cafe-workflow` itself, CAFE CLI/runtime
     Python, workflow state machinery, or host execution behaves incorrectly.
   - **Unconfirmed or transient:** evidence does not yet distinguish a product
     defect from environment, project, provider, or agent behavior.

### Repair only the owned declarative layer

- For a playbook declarative defect, activate `write-cafe-playbook` and edit
  only the writable source-of-truth playbook under `.cafe/playbooks/`, or under
  `src/cafe/data/playbooks/` when the current authorized repository is CAFE.
- For a phase declarative defect, activate `write-cafe-phase` and edit only the
  writable source-of-truth phase/shared/chat skill under `.cafe/skills/`, or
  under `src/cafe/data/skills/` when the current authorized repository is CAFE.
- Never patch generated artifacts, installed package contents, or global CLI
  skill copies as the source fix. After changing bundled authoring skills in
  the CAFE repository, commit the source change; configured post-commit and
  post-merge hooks update installed copies automatically. On other machines,
  CAFE CLI startup performs a per-machine fingerprint check and updates stale
  or missing copies. If automatic sync reports a failure, use
  `cafe skill sync-global` as the explicit recovery command.
- Run each writer skill's strict validation after repair. If planned
  confirmation gates change, rerun `cafe playbook confirmation-gates <id>` and
  reconfirm the issue kickoff contract before the next `cafe make`.
- Do not use either writer skill to modify `use-cafe-workflow`, CAFE runtime
  code, workflow state machinery, or host infrastructure. Do not invent or
  require a `write-cafe-driver` skill.

### Escalate driver and core defects

Do not self-modify a driver or CAFE core defect. Stop before an unsafe or
contract-bypassing workaround, inform the user, and recommend following or
opening an issue at <https://github.com/luyotw/cafe/issues>.

Before recommending a new issue, search open and closed issues read-only and
link an existing match when one exists. Otherwise prepare a sanitized issue
draft containing the CAFE version, CLI/model, playbook/step/intent, exact
command, expected and actual behavior, minimal reproduction, relevant logs,
and any safe workaround. Never include credentials or private project data,
and never create, comment on, or close an upstream issue without explicit user
authorization.

For an unconfirmed or transient failure, retry or ask one focused diagnostic
question instead of labeling it a CAFE defect. Continue through a workaround
only when it is reversible, within mandate, preserves the kickoff contract, and
the user has been informed.

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
