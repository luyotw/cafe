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

- the versioned `delivery_contract` described below;
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
- one `required` or `not_required` proactive-review decision with an
  issue-specific rationale for every agent or hybrid phase; only phases followed
  by an existing scheduled confirmation pause before workflow advancement are
  eligible for `required`, and the smallest useful eligible set is preferred;
- the exact ordered CLI/model chain for every phase, containing one primary and
  zero or more explicitly confirmed fallbacks;
- exactly one operating mode: attached with a positive `poll_interval_seconds`,
  unattended, or event-driven with one non-empty ordered list of distinct,
  conforming CLIs. The first entry is primary: it uses the current user session
  and stores no model, so callbacks cannot override that session's model. Every
  later entry is a forward-only fallback with an exact model selected by the
  user; there is no fixed fallback limit. Event-driven's ordered binding is a
  confirmed field of the sole Driver contract, never `driver/config.yaml`;
- worktree choice and path when using a worktree.

Resolve effective `steps.*.capability_requests` against the package-owned
capability registry. Render each manifest's `setup_questions`: its prompt,
setting, typed choices, observable outcomes, and selected `prepare_args`.
Pass each explicit answer as `--capability-choice SETTING=JSON`. Require every
declared answer and reject unknown settings, duplicate answers, and values
outside the declared typed choices; never infer applicability from step names.
A workflow whose capabilities declare no questions gets no capability questions.
Also inspect the selected playbook's declared prepare fields and gates; do not
invent domain questions or steps. Re-resolve and reconfirm affected choices
when declarations change.

For a new or stale kickoff with a verified corresponding GitHub issue, default
the publication setup question to the manifest choice whose `prepare_args`
enable automatic PR creation. A corresponding issue may come from the current
GitHub initial-input binding or an already persisted and verified issue binding;
a bare issue-like name is insufficient. Without a corresponding issue, default
that question to the manifest's local-only choice. A direct user choice or an
existing valid confirmed choice takes precedence over either default. Always
render the selected value, all declared outcomes, and the exact prepare
arguments for confirmation; the default does not authorize publication before
the complete kickoff is confirmed, and it never authorizes merge or issue
closure.

These settings belong only in generic `issue.yaml`, through the existing
prepare arguments declared by their owner. They do not belong in the Driver
contract. Configuration confirmation covers only the displayed action and
target; it never implies authority for another external action. Follow
`completion_and_authority.md` for ambiguous terminal wording or follow-up work.

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
`scripts/catalog_version_check.py`. When the script exits zero, record its
nested `catalog_check` with the ordinary preflight metadata and use its mismatch
IDs only for the optional reminder. When it exits nonzero, do not read wrapper
fields; route its raw catalog stdout, stderr, and exit code through the existing
catalog preflight first. If that handling permits kickoff to continue, add the
ordinary metadata to the raw catalog payload. Identical content and a catalog
with no eligible project entries are silent. An unavailable runtime check is
visible and recorded but continues with the installed version.
A catalog `over_budget` result with complete discovery retains its bounded IDs
and effective digests without triggering a publication question; incomplete
discovery still fails closed.

Runtime installation and project-to-Global catalog publication are separate
approval scopes. Missing Global entries are ordinary project-only definitions
and produce no reminder. Only when `content_mismatch_entry_ids` is non-empty,
append those IDs as a non-blocking synchronization recommendation in the
effective conversation locale at the very end of the rendered contract. Never
ask a separate pre-kickoff catalog question or infer publication approval from
contract confirmation. If the user separately requests publication, bind its
exact selection to the reported comparison token. After an approved change,
run both checks again and compare effective workflow digests. When effective
behavior changed, present a freshly rendered kickoff contract and obtain
confirmation before preparation or workflow execution.

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
operating mode, or candidate set changes, reconfirm the kickoff contract before
the next workflow execution. Phase model chains are kickoff initial values. The
Driver cannot change a phase model on its own, but must apply an exact phase-only
update for subsequent execution whenever the user explicitly requests one. A
running iteration finishes with the model that started it. This update does not
change the separate event-driven callback chain.

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

### Delivery facts to confirm

Before rendering, read the request and relevant existing evidence, then propose
one complete `delivery_contract` object. Use the user's language. The required
version-1 fields are:

| Field | Content |
| --- | --- |
| `schema_version` | `1` |
| `outcome`, `motivation` | User-visible result and why it matters |
| `in_scope`, `out_of_scope` | Explicit lists; include required edge cases and integrations |
| `acceptance_invariants`, `required_evidence` | Complete conditions and proof needed for acceptance |
| `implementation_direction` | Recommended approach and relevant tradeoffs |
| `constraints` | Explicit lists under `architecture`, `dependencies`, `compatibility`, `quality`, `permissions`, `external_side_effects`, and `cost` |
| `allowed_variations` | Internal substitutions that preserve every requirement |
| `deviation_triggers` | Material changes that require a user-owned handoff |

Use explicit empty lists for categories with no applicable constraint or allowed
variation; never omit a required field. Outcome, motivation, scope, invariants,
evidence, direction and deviation triggers must not be empty. Do not infer
permission or an external-effect approval from product scope. Always preserve:

> The Driver may accept a requirement-equivalent implementation with a smaller
> or simpler implementation footprint. It must not accept reduced user-visible
> behavior, feature scope, acceptance coverage, edge-case coverage, or required
> integrations.

Keep this contract specific about the result and flexible about how agents
reach it. Treat only explicit user requirements, safety or permission
boundaries, external side effects, compatibility promises, and user-visible
behavior as hard invariants. Put anticipated internal choices such as data
shape, thresholds, retry details, helper structure, and equivalent technical
mechanisms in `allowed_variations` unless the user explicitly fixes one. Record
an uncertain technical detail as a working assumption or bounded variation
rather than turning it into a blocker.

A later technical clarification that stays inside `allowed_variations` updates
ordinary phase feedback or artifacts only. It does not replace the Driver
contract, require kickoff reconfirmation, or justify archiving, deleting, or
rebuilding callback dispatch state. Reconfirm only when the user-visible
outcome or scope, authority, external side effects, or an explicitly fixed
invariant materially changes.

Render these facts with the complete kickoff, resolve material ambiguity, and
interpret the user's response semantically in any language. Acknowledgement of
one part does not confirm unreviewed facts. Retain existing explicit decisions;
do not repeatedly ask for unchanged choices. Only the confirmed facts become
`delivery_contract` in the single version-4 durable Driver contract. The nested
Delivery Contract has its own version; no feature-specific sidecar is authority.

Inspect the selected effective entry point, transitions, `initial_input`,
`input_artifacts`, and `output_artifact` declarations. Supply the confirmed
product facts through the entry step's existing initial-input provider/binding
and ordinary input interfaces where needed. Pass outcome/scope/acceptance and
implementation facts, not Driver authority instructions or the policy JSON.
Preserve the original issue input as well. When no such input is declared, use
only an existing supported input boundary; do not synthesize a phase, artifact,
or gate. If required facts cannot be conveyed, raise the existing clarification
handoff. Specification/planning outputs, when present, may refine these facts
but cannot silently reduce or extend them. The same rule applies to diagnosis,
development, drafting, research, or any other entry step.

### Render the proposal

Use the bundled formatter instead of a prose-only summary:

```bash
python3 <skill-dir>/scripts/format_kickoff_contract.py <playbook-id> \
  --issue-name <issue-name> \
  --delivery-contract '<complete version-1 delivery JSON>' \
  --playbook-rationale "<source/evidence, QA decision, and rejected alternative>" \
  --issue-nature <nature> --issue-scale <small|medium|large> \
  --update-preflight '<bounded runtime-update JSON>' \
  --catalog-preflight '<bounded all-catalog JSON>' \
  --driver-mode <attached|unattended|event-driven> \
  [--poll-interval-seconds <positive-integer>] \
  [--event-driver <primary-cli> [--event-driver <fallback-cli>:<exact-model> ...]] \
  --risk-factor "<risk factor; repeat as needed>" \
  --assessment-rationale "<repository evidence for nature and scale>" \
  --phase-rationale "<step>=<capability band, profile/risk evidence, and optional fallback justification>" \
  --proactive-review-decision "<agent-or-hybrid-step>=<required|not_required>:<confirmed rationale>" \
  --effective-locale <locale> \
  --locale-source "<playbook or direct-user-override source>" \
  --repository-content-locale <locale> \
  [--capability-choice <SETTING=JSON> for each declared setup question] \
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
primary model, any configured fallbacks, their config source, exact operating
mode, reactive policy,
mandate boundary, conversation locale source, repository content locale, and
worktree choice. It
re-executes with the Python interpreter that owns `cafe` when the shell
interpreter lacks CAFE dependencies.

Add the existing preflight metadata (`checked_at`, `decision`, and
`post_change_evidence`) to the script's nested `catalog_check` payload after a
zero exit before passing it to `--catalog-preflight`. After a handled nonzero
exit, add them to the raw catalog payload instead; no mismatch reminder exists
for that branch. The formatter contains no fixed-language synchronization
reminder. After formatting, append a reminder in the effective conversation
locale only when `content_mismatch_entry_ids` is non-empty. It lists those IDs,
stays last, and does not become a kickoff decision.

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
  create `driver/config.yaml`: that file is legacy migration evidence only and
  cannot override a contract-managed callback. Its mutable
  `dispatch_state.json` records only the active contract digest, sessions, and
  delivery progress.

  Do not put the mode, CLI, model, session, callback, or any driver control
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
context is only a kickoff proposal. Rename it to `driver_confirmable`, compare it
with the active playbook, and obtain fresh confirmation before persisting it.

## Durable Driver authority

After the user confirms the complete normalized kickoff, activate exactly one
versioned contract at `.cafe/issues/<issue>/driver/contract.json` before the
first Driver entry. The activation command must bind the prepared workflow ID,
timezone-aware confirmation time, confirmer, and the same semantic proposal
that was rendered for confirmation. Rendering alone never writes authority.
That contract contains Driver-owned policy only; generic workflow and capability
configuration remain in `issue.yaml`.

`proactive_review.phase_decisions` is an ordered policy field in that contract,
covering every agent or hybrid phase with `required` or `not_required` and an
issue-specific rationale. It is not a `proactive_review.yaml` sidecar and does
not schedule review work. Capability-owned settings remain only in the generic
`issue.yaml` contract. They are never copied, projected, or validated by the
Driver contract.

On resume, Primary and Backup Drivers must first refresh skill-owned preflight
evidence and validate only the Driver contract before Driver-owned work.
Generic workflow independently validates its own views when it runs.
Metadata-only cache churn may rebuild runtime views; material or unknown Driver
semantic evidence stops for reconfirmation. Session, dispatch, callback
delivery, active CLI, and capability result locations remain runtime state rather than contract
fields.

Delivery facts participate in normalized semantic facts and the proposal digest.
Resume and cross-provider takeover reconstruct them from the same validated
contract through `validate_driver_entry.py`; provider session memory is not
confirmation evidence. Missing Delivery Contract fields, old contract versions,
malformed values, stale identity or a mismatched digest require the existing
reconfirmation path, never defaults or silent migration of product scope.
Generic CAFE workflows without a Driver contract remain usable unchanged.

After explicit reconfirmation, `replace_confirmed_contract` may upgrade a valid
version-3 predecessor using its exact file SHA-256 as the CAS predecessor. It
validates the old identity and digest, adds the confirmed Delivery Contract and
advances the revision atomically. Old contracts are never accepted for entry or
callback authority, and malformed predecessors are never silently overwritten.
