# Kickoff And Preparation

Read this reference before presenting a kickoff, preparing an issue, resuming an
issue whose first workflow execution has not run, or answering a question about
the workflow conversation language. Also read `playbook_selection.md`,
`model_selection.md`, and `strategic_context.md`.

## Conversation locale checklist

- [ ] Resolve the effective locale in this priority order:
  1. a locale the user directly requested for this thread;
  2. a locale reliably inferred from the user's own natural-language messages
     in the current thread;
  3. the active playbook's `playbook.conversation_locale`.
- [ ] Infer a preference when the user's current request clearly uses one
  language, or when multiple user messages consistently use it. Do not infer
  from quoted text, pasted artifacts, code, commands, proper nouns, or an
  isolated token. If the evidence is mixed or ambiguous, use the playbook
  locale.
- [ ] Resolve or select the active playbook using `playbook_selection.md`. When
  no authoritative choice exists, do not apply a builtin default without the
  required repository/issue assessment and recorded rationale.
- [ ] Keep the choice issue-owned. Do not write the selected playbook to
  `.cafe/config.yaml` or `.cafe/strategic_context.yaml`; after confirmation it
  belongs only in `.cafe/issues/<issue-name>/issue.yaml`.
- [ ] Run `cafe playbook confirmation-gates <playbook-id>` and read the
  `Conversation locale:` line, assignable candidate section, and mandatory
  HumanTask section.
- [ ] Treat a configured explicit BCP 47 value as the fallback, not an override
  of a direct or reliably inferred user preference. For `auto`, infer from the
  user's messages using the same rules above.
- [ ] Include the effective value and source in the kickoff, for example:
  `conversation_locale: zh-TW (inferred user preference from current thread)`
  or `conversation_locale: en-US (from playbook: standard)`. Locale is a
  required kickoff field, not a confirmation gate.
- [ ] Apply it to kickoff, clarification, permission, alignment, progress,
  error, and completion messages. Preserve commands, paths, playbook and step
  names, intents, artifact keys, payload fields, and quoted source text.
- [ ] Honor a direct thread language override over every other source. Merely
  writing in another language is an inference signal, not a direct override;
  asking why a language was used is not an override.
- [ ] If asked about the language choice, report the configured value,
  effective value, inference evidence when applicable, and source. Never claim
  this skill lacks a locale rule.

Do not copy the locale into `issue.yaml`. Re-resolve it when starting or
resuming and whenever the playbook changes.

## Repository content locale checklist

- [ ] Treat conversation language and repository content language as separate
  decisions. Use one repository content locale for both documentation and code
  comments by default.
- [ ] Before `cafe init` or any other repository mutation, explicitly ask the
  user to confirm `repository_content_locale`.
- [ ] Recommend the effective conversation locale when the user has not supplied
  a preference, but do not treat inference or a playbook locale as confirmation
  of the repository content language.
- [ ] Preserve programming-language identifiers, commands, paths, protocol
  fields, and established technical terms regardless of the selected locale.
- [ ] If the user explicitly needs documentation and comments to differ, record
  that as a scoped exception instead of making two languages a routine kickoff
  decision.
- [ ] Include the proposed value in the kickoff formatter output. Acceptance of
  the complete kickoff contract explicitly confirms it.
- [ ] Persist the confirmed value in `.cafe/strategic_context.yaml` as repository-
  wide conventions, not in issue-owned workflow state:

  ```yaml
  repository_language:
    content_locale: zh-TW
    confirmed_by: user
    confirmed_at: 2026-08-12
  ```

- [ ] On resume, reuse this confirmed repository-wide value. Reconfirm before
  mutation when it is absent, unconfirmed, or the user requests a change.

## Kickoff contract: first blocking gate

Before `cafe prepare`, any repository mutation, or the first workflow execution,
obtain explicit user confirmation of:

- `playbook_id`;
- `playbook_selection_rationale`, including the independent-QA decision and the
  closest rejected alternative;
- `conversation_locale` with source;
- `repository_content_locale`;
- every assignable planned confirmation gate, partitioned into `user_required`
  and `driver_confirmable`, plus the separate mandatory HumanTask stop list;
- `reactive_user_handoffs`;
- mandate preset, axes, levels, and out-of-mandate list;
- issue nature, scale, and risk factors;
- every resolved phase-skill execution profile, including all possible
  iteration variants at kickoff;
- the exact ordered CLI/model chain for every phase, containing one primary and
  zero or more explicitly confirmed fallbacks;
- `model_adjustment_authority`: either `driver_autonomous` or
  `user_approval_required`;
- `contract_version: 2` and exactly one driver form: attached with a positive
  `poll_interval_seconds`, parameter-free unattended, or delegated with one
  supported CLI and the exact model selected by the user;
- worktree choice and path when using a worktree.

Attached polling applies to proactive `cafe status`, `cafe show`, blackboard,
artifact, or similar liveness checks. Start the timer when a workflow process
starts or resumes, and apply the full confirmed interval before the very first
proactive inspection; there is no shorter startup or warm-up cadence. An empty
early tool yield, session handle, deferred operation id, or generic "still
running" response is transport state rather than an event-driven signal and
must not cause a sub-interval status or artifact poll. Continue waiting for the
remaining interval on the same deferred operation. If no substantive signal
arrives, perform one proactive inspection when the interval elapses, then
restart the timer.

Every proactive inspection must capture and print the current system time with
the result, and the corresponding user update must begin with that same
timestamp. Handle substantive lifecycle output, completion, errors, HumanTasks,
and other event-driven signals immediately; if the process remains active
afterward, restart the timer. Stop the timer when the command exits, the
workflow reaches a user-owned handoff or `done`, or execution stops on an error.
Host-required user communication may occur more often but must not trigger
extra workflow polling.

Before proposing the worktree choice, detect whether the target folder is
already a Git repository. When it is not:

- explain local version history in plain language: it enables recovery and does
  not create GitHub resources or upload files;
- recommend enabling it, but require the kickoff confirmation before mutation;
- use the current checkout for the first task because a worktree would omit the
  uncommitted starting files;
- keep GitHub repository creation, remotes, and push as separate permissions.

Do not reuse another issue's contract or a repository proposal silently. For an
existing issue, honor its confirmed contract and reconfirm only when it is
missing, invalid, or stale.

### Complete runtime and catalog preflight

Before rendering a new contract, and before resuming one that is stale, follow
`project_global_skill_sync.md`. Run `cafe update check --json` and
`cafe catalog check --json`, then record both bounded results. Identical content
and a catalog with no eligible project entries are silent. An unavailable
runtime check is visible and recorded but continues with the installed version.
A catalog `over_budget` result is incomplete and must be narrowed by kind or
entry before kickoff continues; it must not be treated as a no-difference result.

Runtime installation and project-to-Global catalog publication are separate
approval scopes. Bind each decision to its reported comparison token. After an
approved change, run both checks again and compare effective workflow digests.
When effective behavior changed, present a freshly rendered kickoff contract
and obtain confirmation before preparation or workflow execution.

### Derive confirmation gates

1. Run:
   ```bash
   cafe playbook confirmation-gates <playbook-id>
   ```
2. Treat only the reported assignable steps as candidates. Mandatory HumanTask
   steps remain user-owned and never enter the kickoff partition. Both classes
   come from `steps.<step>."on".confirm_output`.
3. Present each candidate by step and purpose. Recommend that all candidates
   stop for the user, then ask the user to assign every candidate to exactly
   one of:
   - `user_required`: stop for the real user;
   - `driver_confirmable`: the driver may verify and continue.
4. Require the two lists to be disjoint and their union to equal the candidates.
   Reject unknown steps, missing candidates, overlaps, role names, and steps
   that do not declare `on.confirm_output`.
5. Present every mandatory HumanTask step as an informational, non-configurable
   user stop. If no assignable candidates exist, explicitly say so without
   implying that mandatory stops are absent.

If the playbook, effective conversation locale, repository content locale,
driver policy, or candidate set changes, reconfirm the contract before the next
workflow execution. A post-phase model-chain change follows the separately
confirmed model-adjustment authority in `model_selection.md`; it does not
silently change a delegated driver's exact model.

`need_clarification` and `need_permission` are reactive interruptions, not
scheduled candidates. `manual_handoff` is routing, not a planned confirmation
gate. Alignment is a proactive driver decision governed by mandate. Record the
reactive policy in the kickoff:

- `need_clarification`: user required unless the exact answer already exists in
  the current thread;
- `need_permission`: user required unless the exact permission already exists
  in the current thread;
- `alignment_checkpoint`: driver-resolvable only when the proposal is clearly
  within confirmed strategy and mandate.

Any other runtime `to_owner=user` baton or `Workflow is waiting for user input`
output is a hard stop.

### Render the proposal

Use the bundled formatter instead of a prose-only summary:

```bash
python3 <skill-dir>/scripts/format_kickoff_contract.py <playbook-id> \
  --issue-name <issue-name> \
  --playbook-rationale "<source/evidence, QA decision, and rejected alternative>" \
  --issue-nature <nature> --issue-scale <small|medium|large> \
  --model-adjustment-authority <driver_autonomous|user_approval_required> \
  --update-preflight '<bounded runtime-update JSON>' \
  --catalog-preflight '<bounded all-catalog JSON>' \
  --driver-mode <attached|unattended|delegated> \
  [--poll-interval-seconds <positive-integer>] \
  [--delegated-cli <claude|codex|gemini|copilot|cursor-agent> \
   --delegated-model <exact-model>] \
  --risk-factor "<risk factor; repeat as needed>" \
  --assessment-rationale "<repository evidence for nature and scale>" \
  --phase-rationale "<step>=<capability band, profile/risk evidence, and optional fallback justification>" \
  --effective-locale <locale> \
  --locale-source "<playbook or direct-user-override source>" \
  --repository-content-locale <locale> \
  --user-required <steps...> \
  --driver-confirmable <steps...> \
  --worktree .cafe/worktrees/<issue-name>
```

When the target folder is not yet a Git repository and initialization is part
of this kickoff, replace the final `--worktree ...` argument with
`--current-checkout`. The rendered contract must show the current checkout for
that first task; do not ask the user to approve a worktree that cannot safely
contain the starting files.

`--playbook-rationale` is required even when the user or a durable contract
already selected the playbook; in that case record the authoritative source and
why the selected graph still satisfies current repository requirements.

Pass `--phase-chain <step>=<primary-cli>:<exact-model>` once for every
agent-executed phase that is not already fully resolved by `--phase-config`.
Append `,<fallback-cli>:<exact-model>` for each fallback the user confirms.
Fallbacks are optional; a primary-only chain is valid and means a failure stops
the workflow instead of switching CLIs.
The formatter requires exactly the fields applicable to the selected driver
mode and rejects fields from another mode. It has no built-in provider or
model defaults. It
rejects a missing primary, an unresolved model, and an unsupported CLI. It
validates chain structure only; it does not validate model suitability. Pass one
`--phase-rationale <step>=<text>` for every agent-executed phase. The formatter
rejects missing, unknown, or duplicate rationales and displays them beside the
chain. Use the capability band, phase profile, issue assessment, current
provider documentation, and preflight evidence to justify that each selected
model satisfies the displayed requirements. This judgment remains driver-owned
rather than a runtime model registry, and the formatter labels the model-chain
table `driver-assessed`.

Pass an option with no step values for an explicit empty list. The formatter
validates the partition and includes every phase, role, skill, scheduled gate,
owner, stop behavior, resolved skill execution profile, exact
primary model, any configured fallbacks, their config source, autonomous
adjustment authority, exact v2 driver policy, reactive policy,
mandate boundary, conversation locale source, repository content locale, and
worktree choice. It
re-executes with the Python interpreter that owns `cafe` when the shell
interpreter lacks CAFE dependencies.

If the user already chose values in the current request, render and restate them
for confirmation rather than asking again.

## Preparation checklist

- [ ] Inventory strategic documents and authority using
  `strategic_context.md`; co-create any required missing document before
  workflow execution.
- [ ] Detect whether the folder is a Git repository. If it is, check
  `git status --short --branch`.
- [ ] If the folder is not a Git repository, record that read-only finding,
  include local version-history initialization in the kickoff, and after
  confirmation pass `--init-git` to `cafe prepare`. Do not request a worktree
  for that first task.
- [ ] Verify `repository_content_locale` was explicitly confirmed and persist
  it in `.cafe/strategic_context.yaml`.
- [ ] Complete the issue assessment and model preflight from
  `model_selection.md`; do not propose a model that already failed preflight.
- [ ] If needed, initialize project-owned files with `cafe init --no-interactive`.
  This creates the project files without turning the active issue's playbook
  into a repository setting. Verify that `.cafe/config.yaml` has no playbook
  key before continuing.
- [ ] For an existing initialized repository, recommend a worktree at
  `.cafe/worktrees/<issue-name>` by default. If the user accepts the recommended
  kickoff unchanged, worktree creation is approved. For an approved first Git
  initialization, instead record and render the `current checkout` choice.
- [ ] Prepare non-interactively:

  ```bash
  cafe prepare <issue-name> --playbook <playbook-id> --no-interactive \
    --input-method=manual \
    --rigor=medium --spec-template=auto --plan-template=default \
    --driver-contract-version=2 --driver-mode=<mode> \
    <applicable-driver-options> \
    --worktree .cafe/worktrees/<issue-name>
  ```

  For the first task in a folder whose Git initialization was approved, omit
  `--worktree` and use:

  ```bash
  cafe prepare <issue-name> --playbook <playbook-id> --no-interactive \
    --init-git --input-method=manual --rigor=medium \
    --spec-template=auto --plan-template=default \
    --driver-contract-version=2 --driver-mode=<mode> \
    <applicable-driver-options>
  ```

  For a GitHub issue:

  ```bash
  cafe prepare <issue-name> --playbook <playbook-id> --no-interactive \
    --input-method=github --issue-id=<number> --rigor=medium \
    --spec-template=auto --plan-template=default \
    --driver-contract-version=2 --driver-mode=<mode> \
    <applicable-driver-options> \
    --worktree .cafe/worktrees/<issue-name>
  ```

- [ ] If the user declined a worktree, omit `--worktree`. Never silently fall
  back to the main checkout after worktree creation fails.
- [ ] Enter the reported worktree before running workflow commands.
- [ ] Verify that `cafe prepare` persisted the active `playbook_id` and exact
  driver policy, then add the confirmation contract, reactive handoff policy,
  issue assessment, and model-adjustment authority to
  `.cafe/issues/<issue-name>/issue.yaml` in the active checkout before the first
  workflow execution:

  ```yaml
  playbook_id: standard
  preflight:
    runtime_update:
      checked_at: 2026-08-27T12:00:00Z
      status: current
      installed_version: 0.3.2
      latest_version: 0.3.2
      comparison_token: <content-bound-token>
      decision: not_needed
      post_change_evidence: not_applicable
    catalogs:
      checked_at: 2026-08-27T12:00:01Z
      status: identical
      comparison_token: <content-bound-token>
      effective_digests:
        playbook: <digest>
        phase: <digest>
        agent: <digest>
      decision: not_needed
      post_change_evidence: not_applicable
    behavior_changed: false
    reconfirmed_at: null
  confirmation_contract:
    user_required: [spec, plan]
    driver_confirmable: []
    confirmed_by: user
    confirmed_at: 2026-07-16
  reactive_user_handoffs:
    need_clarification: user_required
    need_permission: user_required
    alignment_checkpoint: driver_resolvable_when_clear
  issue_assessment:
    nature: feature/integration
    scale: medium
    risk_factors: [public contract, integration coverage]
  model_adjustment:
    authority: driver_autonomous
    confirmed_by: user
    confirmed_at: 2026-08-13
  contract_version: 2
  driver:
    mode: delegated
    cli: codex
    model: gpt-5.6-codex
  ```

  For an older prepared issue without a complete valid v2 policy, collect a
  fresh explicit form and use `cafe update-driver-policy`. Do not infer,
  translate, prefill, or retain values from `driver_execution` or any other
  legacy shape.

- [ ] Install the confirmed ordered phase chains in the active worktree with
  `scripts/write_phase_config.py`, then verify the effective config as described
  in `model_selection.md`.
- [ ] Re-run `cafe playbook confirmation-gates <playbook-id>` and verify the
  locale and exact candidate partition before the first workflow execution.
- [ ] If preparation accidentally omitted an approved worktree, repair or
  recreate preparation before the first workflow execution; do not discard the
  issue configuration or silently continue in the main checkout.

A legacy `mandate.confirmation_contract.agent_confirmable` value in strategic
context is only a kickoff proposal. Rename it to `driver_confirmable`, compare it
with the active playbook, and obtain fresh confirmation before persisting it.
