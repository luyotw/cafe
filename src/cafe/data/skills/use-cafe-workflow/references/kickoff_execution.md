# Kickoff execution and direct formatter reference

Read the preparation policy in `kickoff.md` first. These are the unchanged
operating instructions for direct formatter invocation and for preparation,
activation or polling after the required confirmation. A proposal-only session
does not execute these operations. Runtime/catalog and action-specific authority
boundaries still apply.

### Attached execution polling

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

### Formatter CLI example

The consolidated helper already supplies typed inputs and invokes this owner.
Read this direct-CLI example only when using the formatter without that helper.

```bash
python3 <skill-dir>/scripts/format_kickoff_contract.py <playbook-id> \
  --issue-name <issue-name> \
  --delivery-contract '<compact version-3 product JSON without closeout_plan>' \
  --deliver '[["literal-executable", "literal-argument"]]' \
  --deliver-description "<action and target in the conversation language>" \
  --cleanup '[["literal-executable", "literal-argument"]]' \
  --cleanup-description "<action and target in the conversation language>" \
  --update-preflight '<bounded runtime-update JSON>' \
  --catalog-preflight '<bounded all-catalog JSON>' \
  --manager-mode <attached|unattended|event-driven> \
  [--poll-interval-seconds <positive-integer>] \
  [--event-manager <primary-cli> [--event-manager <fallback-cli>:<exact-model> ...]] \
  [--proactive-review-decision "<eligible-step>=<required|not_required>"] \
  --effective-locale <locale> \
  --locale-source "<playbook or direct-user-override source>" \
  --repository-content-locale <locale> \
  [--capability-choice <SETTING=JSON> for each declared setup question] \
  --user-required <steps...> \
  --manager-confirmable <steps...> \
  --worktree .cafe/worktrees/<issue-name>
```

When the target folder is not yet a Git repository and initialization is part
of this kickoff, replace the final `--worktree ...` argument with
`--current-checkout`. The rendered contract must show the current checkout for
that first task; do not ask the user to approve a worktree that cannot safely
contain the starting files.

Assess playbook suitability, issue risk and model capability before proposing
execution settings. Do not require rationale, assessment or preflight records
as user-confirmed fields or persist them in the Manager contract.

Pass `--phase-chain <step>=<primary-cli>:<exact-model>` once for every
agent-executed phase that is not already fully resolved by `--phase-config`.
Append `,<fallback-cli>:<exact-model>` for each fallback the user confirms.
Fallbacks are optional; a primary-only chain is valid and means a failure stops
the workflow instead of switching CLIs.
The formatter requires exactly the fields applicable to the selected manager
mode and rejects fields from another mode. It has no built-in provider or
model defaults. It
rejects a missing primary, an unresolved model, and an unsupported CLI. It
validates chain structure only; it does not validate model suitability.
Use the capability band, resolved execution profile, issue
assessment, provider documentation, and model preflight to justify each choice.
The user-facing table shows the exact primary and fallback chain without
repeating selection diagnostics. Model suitability remains Manager-assessed.

Pass an option with no step values for an explicit empty list. The formatter
validates the partition and shows every phase, scheduled gate,
owner, stop behavior, exact primary model, any configured fallbacks, operating
mode, reactive policy,
task-specific authority, conversation locale source, repository content locale, and
worktree choice. It
re-executes with the Python interpreter that owns `cafe` when the shell
interpreter lacks CAFE dependencies.

Add the existing preflight metadata (`checked_at`, `decision`, and
`post_change_evidence`) to the script's nested `catalog_check` payload after a
zero exit before passing it to `--catalog-preflight`. After a handled nonzero
exit, add them to the raw catalog payload instead; no mismatch reminder exists
for that branch. When `content_mismatch_entry_ids` is non-empty, the formatter
renders the localized reminder with those exact IDs immediately before its
confirmation prompt and final progress block. The reminder does not become a
kickoff decision.

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
    <confirmed playbook-owned prepare arguments> \
    <confirmed capability-owned prepare arguments, if any> \
    --worktree .cafe/worktrees/<issue-name>
  ```

  For the first task in a folder whose Git initialization was approved, omit
  `--worktree` and use:

  ```bash
  cafe prepare <issue-name> --playbook <playbook-id> --no-interactive \
    --init-git <confirmed playbook-owned prepare arguments> \
    <confirmed capability-owned prepare arguments, if any>
  ```

  For a GitHub issue:

  ```bash
  cafe prepare <issue-name> --playbook <playbook-id> --no-interactive \
    --input-method=github --issue-id=<number> --rigor=medium \
    --spec-template=auto --plan-template=default \
    <confirmed capability-owned prepare arguments, including the default \
    automatic-PR argument when the selected playbook declares it> \
    --worktree .cafe/worktrees/<issue-name>
  ```

- [ ] If the user declined a worktree, omit `--worktree`. Never silently fall
  back to the main checkout after worktree creation fails.
- [ ] Enter the reported worktree before running workflow commands.
- [ ] Verify that `cafe prepare` persisted the active `playbook_id`, then add
  the confirmation contract, reactive handoff policy,
  and generic workflow configuration to
  `.cafe/issues/<issue-name>/issue.yaml` in the active checkout before the first
  workflow execution:

  The following is generic issue configuration, not `manager/contract.json`.
  Its existing preflight records are not user-confirmed contract fields.

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
    manager_confirmable: []
    confirmed_by: user
    confirmed_at: 2026-07-16
  ```

  For every resolved capability setup question, pass only the selected
  choice's declared prepare arguments and verify the exact typed answer at its
  declared setting in `issue.yaml`. Do not pass arguments for absent questions.
  Missing, changed, or stale answers require a freshly rendered and confirmed
  affected choice; never replace missing answers with defaults. Leave legacy
  configuration interpretation and migration to its owning capability/runtime.

  When the confirmed mode is event-driven, launch the trusted callback after
  this contract is written. It loads the current issue contract immediately
  before dispatch and derives its primary CLI plus fallback CLI/model view in
  memory. Do not
  create `manager/config.yaml`: that file is legacy migration evidence only and
  cannot override a contract-managed callback. Its mutable
  `dispatch_state.json` records session CLI/model identities, event routing
  history, and delivery progress. Its legacy digest field does not control
  callback continuation; the current contract controls new dispatch.

  Do not put the mode, CLI, model, session, callback, or any manager control
  setting in `issue.yaml`. Confirm that every entry reports `event-driven
  session-and-dispatch: conforming` before accepting the contract. When the
  primary is Codex and this command runs from a Codex App thread, that thread
  is a best-effort first-session hint recorded only in callback runtime state;
  a persisted acquired session wins, binding failure does not block workflow
  execution, and no fallback inherits the host binding.

  Confirm these two separate lifecycle boundaries explicitly. An unbound entry
  first receives a bootstrap exactly equivalent to `say "HI"`; Codex, Claude,
  Gemini, Cursor, and Copilot must each return a provider-created session ID.
  That ID is persisted in `dispatch_state.json` before the actual callback is
  sent. An existing acquired session wins over a new host hint; otherwise a
  successfully recorded first Codex host binding is reused without bootstrap.
  The bootstrap never
  counts as event delivery or acceptance; only actual callback durable
  acceptance can stop routing and select the sticky active entry. The provider
  acknowledgement is bound to the exact event identity in that dispatched
  invocation. Copilot never receives a caller-selected new-session ID.

- [ ] Install the confirmed ordered phase chains in the active worktree with
  `scripts/write_phase_config.py`, then verify the effective config as described
  in `model_selection.md`.
- [ ] Re-run `cafe playbook confirmation-gates <playbook-id>` and verify the
  locale and exact candidate partition before the first workflow execution.
- [ ] If preparation accidentally omitted an approved worktree, repair or
  recreate preparation before the first workflow execution; do not discard the
  issue configuration or silently continue in the main checkout.

A legacy `mandate.confirmation_contract.agent_confirmable` value in strategic
context is only a kickoff proposal. Rename it to `manager_confirmable`, compare it
with the active playbook, and obtain fresh confirmation before persisting it.

## Durable Manager authority

After the user confirms the complete normalized kickoff, activate exactly one
schema-version-8 contract at
`.cafe/issues/<issue>/manager/contract.json` before the first Manager entry. The
activation command must bind the prepared workflow ID,
timezone-aware confirmation time, confirmer, and the same semantic proposal
that was rendered for confirmation. Rendering alone never writes authority.
That contract contains Manager-owned policy only; generic workflow and capability
configuration remain in `issue.yaml`.

`proactive_review.phase_decisions` is an ordered normalized policy field in that
contract. Eligible scheduled pauses use the confirmed default or explicit
override; ineligible agent or hybrid phases are recorded as derived
`not_required` entries without becoming kickoff choices. It is not a
`proactive_review.yaml` sidecar and does not schedule review work.
Capability-owned settings remain only in the generic
`issue.yaml` contract. They are never copied, projected, or validated by the
Manager contract.

On resume, Primary and Backup Managers must first refresh skill-owned preflight
evidence and validate only the Manager contract before Manager-owned work.
Generic workflow independently validates its own views when it runs.
Metadata-only cache churn may rebuild runtime views; material or unknown Manager
semantic evidence stops for reconfirmation. Session, dispatch, callback
delivery, active CLI, and capability result locations remain runtime state rather than contract
fields.

Delivery facts participate in the proposal digest and the fresh-policy comparison.
The current contract does not store assessment, mandate, rationale, preflight,
or duplicate semantic/material projections. Build `semantic_facts.effective_policy`
for a current entry check from the complete applicable policy; do not create a
second durable policy record. Runtime/catalog checks still occur outside the
contract and their diagnostics do not grant authority.
Resume and cross-provider takeover reconstruct them from the same validated
contract through `validate_manager_entry.py`; provider session memory is not
confirmation evidence. Missing Delivery Contract fields, old contract versions,
malformed values, stale identity or a mismatched digest require the existing
reconfirmation path, never defaults or silent migration of product scope.
Generic CAFE workflows without a Manager contract remain usable unchanged.

After explicit reconfirmation, `replace_confirmed_contract` may upgrade a valid
version-3 or version-4 predecessor using its exact file SHA-256 as the CAS
predecessor. It validates the old identity and digest, writes the newly confirmed
compact policy and advances the revision atomically. Old contracts cannot grant
ordinary Manager-entry or task-decision authority. Their validated event-transport
settings may still be read through the existing bounded callback projection;
that is not activation or an upgrade. Malformed predecessors are never silently
overwritten.

### Refresh runtime constraints

Before kickoff, retry/resume or diagnosis, refresh the package-owned view with
`cafe constraints list --cli <effective-cli> --consumer authority --json`.
For an existing issue use `cafe constraints list --issue <issue> --step <step>
--json`; inspect every configured CLI alternative rather than assuming that the
primary's limits apply to a fallback. For callback handling include
`--consumer callback --operation event-driver`. Use `cafe constraints show <ID>
--json` for source, detection, mitigation and history. This is read-only evidence,
not authority to alter a chain, answer a HumanTask, stop a worker or run a command.

An agent's fenced JSON `kind=constraint_assistance` in its existing output or
handoff is the exact Manager-assisted request. Require unique non-empty
`constraint_ids` and non-empty `workload`, `reason`, `requested_support` and
`completion_evidence`. Refresh the same applicable constraints, verify the IDs,
and route support through existing HumanTask/capability/worker controls. Never
execute the request or advance the workflow solely because this declaration
exists. Progress visibility is separate from exit status and completion proof.
Record the IDs in interruption/recovery communication.

Runtime constraints evidence is version 1 in the optional
`provenance.runtime_constraints` field of Manager schema 8. It is refresh evidence,
not a user-confirmed policy field or permission. Old schema-8 contracts remain
readable, but absence of this evidence yields `unknown` freshness and cannot
establish eligibility for reuse. Use existing preflight/reconfirmation recovery;
do not fill a missing historical digest with today's value and call it unchanged.
Metadata-only registry edits preserve semantic identity. Applicable boundary,
action, authority, scope or external-validity changes invalidate it.
