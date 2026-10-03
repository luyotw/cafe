# Kickoff And Preparation

Use `kickoff_inputs.md` for the preparation CLI and editable draft. Apply this
reference and the model, strategy and playbook policies to the current request.
Execution and activation instructions are in `kickoff_execution.md`.

## Reuse kickoff preferences and evidence

For a new issue, start with the staged `prepare_kickoff.py` path in
`kickoff_inputs.md`. Have `draft` create the request and prefill it before
supplying product decisions. It performs discovery and assembly itself; use its
reports before repeating candidate-listing, preference, delivery, or model-evidence
reads. Treat its catalog, preference,
delivery, and model reports as sourced facts with explicit freshness and gaps;
they do not decide issue scope, acceptance criteria, playbook suitability,
model capability, or action authority. The selected graph and all unresolved
Manager decisions remain Manager-owned.

Use `draft --issue-id <id> --output <draft.json>` for the normal Manager path,
adding `--playbook-id` when already selected. Edit unresolved fields in that
draft; `assemble --request-file <draft.json> --summary --draft-output
<updated-draft.json>` refreshes it after choosing a playbook or repairing inputs.
The compact reports
preserve every candidate's decision-relevant facts and invalid diagnostics,
evidence status, source fingerprints or provenance, ages, and full-inspection
commands; assembly also preserves the selected graph, missing decisions, and
exact formatter inputs. Full candidate and diagnostic details remain available
through the referenced inspection commands or the default full reports. Do
not repeat unchanged source inspection after a validated hit. A cached hit
never skips first-time issue scope, strategy, action-target, suitability, or
authority decisions.

After selecting an eligible graph and resolving the required decisions, pass
those decisions and normalized formatter inputs through `assemble`, then use
`render` to produce the complete contract with the existing formatter. Do not
render an incomplete assembly, omit reported gaps, or start preparation or a
workflow before the complete contract is confirmed.

Read preferences by their user or repository scope. Apply the preference prompts
in `kickoff_inputs.md`: ask whether to save applicable unset preferences, and
remind the user how to save explicit choices that differ from the effective
defaults. These prompts save preferences only for the current project through
repository scope; they never offer or write user-wide preferences. Save a value
only when the user explicitly requests reusable preference; never turn an inferred language,
one-off issue answer, model suitability decision, or action authorization into a
saved preference. Use the documented `inspect`, `set`, and `clear` operations
for the named scope. For evidence, inspect before refresh, gather and validate
the underlying evidence yourself, and refresh only from that evidence. A
missing, stale, changed, contradictory, or incomplete record remains a gap; a
successful operational probe is not model-capability evidence.

On resume, the confirmed workflow contract and generic workflow state remain
authoritative. Saved preferences and cached facts may inform a newly proposed
contract only; they never rewrite a confirmed contract or pending task.

## Conversation locale checklist

`docs/language-policy.md` is the single source of truth for precedence,
persistence, the language-change scope, and the fallback rules. This checklist
only describes what the Manager does; it never states a competing rule.

- [ ] Read `docs/language-policy.md` before answering any question about the
  workflow conversation language.
- [ ] Supply, do not decide. For a **new** workflow, infer a preference only
  from the user's own natural-language messages, and pass it into the generic
  contract with its tier: `--conversation-locale <tag>
  --conversation-locale-source explicit|inferred` on `cafe prepare` or
  `cafe workflow`. Never claim `explicit` for an inferred preference.
- [ ] Infer a preference when the user's current request clearly uses one
  language, or when multiple user messages consistently use it. Do not infer
  from quoted text, pasted artifacts, code, stack traces, logs, commands,
  proper nouns, or an isolated token such as `1` or `ok`. If the evidence is
  mixed or ambiguous, supply nothing and let the playbook default apply.
- [ ] On **resume**, read the effective generic value and source from the
  workflow's own state. Do not re-resolve it, and do not supply a preference in
  order to change it. Your `locales.conversation` snapshot mirrors that value;
  it is not a competing resolver.
- [ ] Resolve or select the active playbook using `playbook_selection.md`. When
  no authoritative choice exists, do not apply a builtin default without the
  required repository/issue assessment.
- [ ] Keep the choice issue-owned. Do not write the selected playbook to
  `.cafe/config.yaml` or `.cafe/strategic_context.yaml`; after confirmation it
  belongs only in `.cafe/issues/<issue-name>/issue.yaml`.
- [ ] Use validated selected-graph confirmation gates from the preparation
  summary, or run `cafe playbook confirmation-gates <playbook-id>` for a gap. Read the
  `Conversation locale:` line, assignable candidate section, and mandatory
  HumanTask section. That `playbook.conversation_locale` value is the third
  precedence tier, not an override of a supplied user preference.
- [ ] Include the effective value and source in the kickoff, for example:
  `conversation_locale: zh-TW (inferred user preference from current thread)`
  or `conversation_locale: en-US (from playbook: standard)`. Locale is a
  required kickoff field, not a confirmation gate.
- [ ] Apply it to kickoff, clarification, permission, alignment, progress,
  error, and completion messages. Preserve commands, paths, playbook and step
  names, intents, artifact keys, payload fields, and quoted source text.
- [ ] Distinguish the two requests. "Reply to me in X, just this once" applies
  to that reply only and changes nothing stored. A request to change the
  *workflow* language is the explicit operation
  `cafe workflow --set-conversation-locale <tag> --conversation-locale-source
  explicit`; it never rewrites an already-pending task.
- [ ] Merely writing in another language is an inference signal for a new
  workflow, not a change request; asking why a language was used is neither.
- [ ] If asked about the language choice, report the stored effective value and
  its source, the playbook value, and the inference evidence when applicable.
  Never claim this skill lacks a locale rule.

Do not copy the locale into `issue.yaml`. Do not re-resolve it on resume or when
the playbook changes: the stored value stands until the explicit change
operation replaces it.

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
- [ ] Keep engineering artifacts — spec, plan, review, and PR prose — in this
  repository content language even when the conversation language differs.

## Repository-informed deliver and cleanup plan

At the beginning of every new kickoff, inspect the validated delivery summary
from assembly: source-backed conventions, routes, discovery coverage, freshness
and gaps. Sufficient valid facts satisfy inspection of the unchanged documentation,
CI/CD configuration and scripts they cover; apply them to the current endpoint.
Open original sources only for uncovered facts, invalidation or contradictions,
and verify current action targets separately. A valid cached delivery template
prefills the proposed actions with current issue values; standard worktree and
cleanup conventions are also prefilled. Review these values against the current
request. A hit does not authorize execution. Do not enumerate vendors or invent
a generic merge/deploy route when the repository has no reusable template.

Discover the intended end state beyond merely opening a PR. Propose the
repository-appropriate delivery and cleanup actions for user approval; an action
not yet authorized is not a reason to leave it out of the proposal. Do not
invent a PR-only endpoint or exclusions for merge, issue closure or worktree
removal to avoid asking for that approval. Respect an explicit user choice to
stop at a PR, preserve resources, or exclude an action. Repository context
informs the recommendation; only user confirmation authorizes execution.

When delivery includes merging a GitHub PR, propose a merge commit by default:
`gh pr merge --merge` from the verified issue worktree, or with an exact verified
PR selector. This preserves the branch commits and fixes the strategy for
non-interactive execution. Propose `--squash` or `--rebase` only when the user
explicitly chose that strategy. If repository policy disallows merge commits,
resolve the available strategy with the user before rendering the contract.
An existing confirmed closeout plan takes precedence; changing its strategy
requires reconfirmation.

Default the cleanup proposal to closing the verified, bound GitHub issue and
then running `cafe close`, in that order. Use the issue's verified numeric ID
in the first exact argv array:

```yaml
cleanup:
  - argv: [gh, issue, close, "123"]
  - argv: [cafe, close]
```

`gh issue close` is applicable only when the issue has a verified GitHub
binding; when it does not, omit that command but retain `cafe close` as the
default. An explicit user choice to preserve the GitHub issue or CAFE issue
state overrides the default. Never use an issue-like name, an unresolved
placeholder, or a guessed ID. The complete proposal remains subject to the
same kickoff confirmation as every other external action.

Turn the discovered route into two ordered lists of exact host-side commands:

```yaml
deliver:
  - argv: [command, argument]
cleanup:
  - argv: [command, argument]
```

Both fields are required in every new contract. Use an explicit `[]` for
a stage with genuinely no remaining action or one the user explicitly excludes;
make the reason clear in the existing scope or constraints. Lack of CI/CD
configuration, lack of existing permission, or an unresolved future target does
not mean nothing remains. Do not fill `[]` as a discovery fallback, omit the
field, or invent a no-op. In particular, do not use `[]` as a substitute for
the default issue closure and `cafe close` cleanup route without recording the
user's exclusion or the inapplicable GitHub binding.

Every argument must be concrete at kickoff: no shell strings, templates,
placeholders, or future identifiers that will be filled in later. When a future
identifier is unavailable, use an existing stable selector only after verifying
it identifies the intended target, or obtain a fresh confirmation once the
concrete command exists. Inspect whether integration already triggers delivery
before proposing another deployment command.

Validate lifecycle commands before presenting the contract. `cafe close` must
be the final cleanup command and use the literal `cafe` executable. Its
`--squash` and optional message arguments are local-review behavior only; reject
them when the confirmed automatic-PR capability choice enables PR creation.
This rule does not apply when a remote PR merge command uses the same flag to
select its merge strategy rather than the local close path.

When the intended action or target is unresolved, identify the missing choice
and ask a focused question instead of presenting an empty plan as settled. Do
not activate a plan with an unresolved stage: obtain concrete argv or a verified
stable selector, then render it for confirmation. The user may instead choose
a narrower endpoint, such as stopping at a PR; record that choice in the
existing scope or constraints. A later expansion requires a newly confirmed
plan, not filling in the original `[]` after kickoff.

Present both exact command lists, grounded in the repository context above, without
adding an evidence report to the contract. The user confirms the complete
kickoff, including their command order and effects. That
confirmation is durable authority for the Manager to execute exactly those arrays
at closeout; it is not authority for a changed command, reordered command, or
materially changed target/effect. Never silently discard restrictions from an
older confirmed contract; a user reconfirmation is required to replace it with
the compact contract below.

## Kickoff contract: first blocking gate

Before `cafe prepare`, any repository mutation, or the first workflow execution,
obtain explicit user confirmation of:

- the versioned `delivery_contract` described below, including the user-confirmed
  exact `deliver` and `cleanup` argv arrays derived from repository evidence;
- `playbook_id`;
- `conversation_locale` with source;
- `repository_content_locale`;
- every assignable planned confirmation gate, partitioned into `user_required`
  and `manager_confirmable`, plus the separate mandatory HumanTask stop list;
- overall `need_clarification` ownership, defaulting new proposals to
  `manager_confirmable`; present finer clarification ownership only when the user
  requests it. Declare ownership by exact phase and task ID for those overrides,
  using `--task-manager-confirmable PHASE:TASK_ID` or
  `--task-user-required PHASE:TASK_ID`. Explicit task ownership takes precedence
  over the overall policy. Other undeclared tasks remain user-owned;
- `reactive_user_handoffs`;
- the effective proactive-review decision for every agent or hybrid phase with
  an existing scheduled confirmation pause. Default every assignable scheduled
  confirmation gate to `manager_confirmable` with proactive review `required`;
  default mandatory gates to `required` while they remain user-owned, and let
  direct user overrides take precedence. Normalize ineligible phases internally
  to `not_required`; they require no kickoff choice;
- the exact ordered CLI/model chain for every phase, containing one primary and
  zero or more explicitly confirmed fallbacks;
- exactly one operating mode: attached with a positive `poll_interval_seconds`,
  unattended, or event-driven with one non-empty ordered list of distinct,
  conforming CLIs. The first entry is primary: it uses the current user session
  and stores no model, so callbacks cannot override that session's model. Every
  later entry is a forward-only fallback with an exact model selected by the
  user; there is no fixed fallback limit. Event-driven's ordered binding is a
  confirmed field of the sole Manager contract, never `manager/config.yaml`;
- worktree choice and path when using a worktree.

`format_kickoff_contract.py` renders the complete user-facing kickoff, including
the confirmation prompt and planned graph from the shared
`render_workflow_progress.py` implementation. Present its complete stdout in the
effective conversation language for the initial confirmation request instead of
replacing it with a prose summary. Follow the translation boundary below and
`workflow_progress.md`; do not recreate a phase list or append a second diagram.
The kickoff has no runtime execution evidence, so
phases, scheduled Manager reviews, and closeout items are all pending.

For a new workflow, use event-driven as the proposed default unless the user
explicitly chooses another mode or an existing confirmed issue contract already
fixes it. Render the proposed mode with the complete kickoff for confirmation;
a default is not confirmation or execution authority.

Resolve effective `steps.*.capability_requests` against the package-owned
capability registry. Render each manifest's `setup_questions`: its prompt,
setting, selected typed value, observable outcome, and selected `prepare_args`.
Explain alternative choices when the user asks to change the proposal.
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
render the selected value, its declared outcome, and the exact prepare
arguments for confirmation; the default does not authorize publication before
the complete kickoff is confirmed, and it never authorizes merge or issue
closure.

These settings belong only in generic `issue.yaml`, through the existing
prepare arguments declared by their owner. They do not belong in the Manager
contract. Configuration confirmation covers only the displayed action and
target; it never implies authority for another external action. Follow
`completion_and_authority.md` for ambiguous terminal wording or follow-up work.

### Checkout and existing contracts

For an existing initialized repository, recommend a worktree at
`.cafe/worktrees/<issue-name>` by default. If the user accepts the recommended
kickoff unchanged, worktree creation is approved. An explicit user choice takes
precedence; never silently fall back after worktree creation fails.

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

Before `cafe prepare --no-interactive`, complete the Manager-managed runtime
update decision in `project_global_skill_sync.md`. Present an available update
to the user and obtain its explicit answer before installation; the command
itself must never prompt. Record the decision and fresh post-apply check before
continuing preparation.

Runtime installation and project-to-Global catalog publication are separate
approval scopes. Missing Global entries are ordinary project-only definitions
and produce no reminder. Only when `content_mismatch_entry_ids` is non-empty,
have the formatter include those IDs as a non-blocking synchronization
recommendation in the effective conversation locale after the contract details
and before its confirmation prompt and final progress block. Never ask a
separate pre-kickoff catalog question or infer publication approval from
contract confirmation. If the user separately requests publication, bind its
exact selection to the reported comparison token. After an approved change, run
both checks again and compare effective workflow digests. When effective
behavior changed, present a freshly rendered kickoff contract and obtain
confirmation before preparation or workflow execution.

### Derive confirmation gates

1. Use `selected_graph.confirmation_gates`,
   `selected_graph.mandatory_confirmation_gates` and the resolved step HumanTasks
   from the current validated assembly. If these facts are missing or invalid,
   use the existing owner query:
   ```bash
   cafe playbook confirmation-gates <playbook-id>
   ```
2. Treat only the reported assignable steps as candidates. Mandatory HumanTask
   steps remain user-owned and never enter the kickoff partition. Both classes
   come from `steps.<step>."on".confirm_output`.
3. Present each candidate by step and purpose. Default every candidate to
   `manager_confirmable` with proactive review `required`, then allow the user to
   override any candidate into exactly one of:
   - `user_required`: stop for the real user;
   - `manager_confirmable`: the manager may verify and continue.
4. Require the two lists to be disjoint and their union to equal the candidates.
   Reject unknown steps, missing candidates, overlaps, role names, and steps
   that do not declare `on.confirm_output`.
5. Present every mandatory HumanTask step as an informational, non-configurable
   user stop with proactive review `required` by default. A clean review never
   replaces its user decision. If no assignable candidates exist, explicitly
   say so without implying that mandatory stops are absent.
6. Do not ask for proactive-review decisions on agent phases without a
   scheduled confirmation pause. The formatter normalizes those phases to
   `not_required` so durable coverage remains complete.

If the playbook, effective conversation locale, repository content locale,
operating mode, or candidate set changes, reconfirm the kickoff contract before
the next workflow execution. Phase model chains are kickoff initial values. The
Manager cannot change a phase model on its own, but must apply an exact phase-only
update for subsequent execution whenever the user explicitly requests one. A
running iteration finishes with the model that started it. This update does not
change the separate event-driven callback chain.

`need_clarification` and `need_permission` are reactive interruptions, not
scheduled candidates. `manual_handoff` is routing, not a planned confirmation
gate. Alignment is a proactive manager decision governed by mandate. Record the
overall reactive policy in the kickoff, with task overrides only when requested:

- Default new proposals to `need_clarification: manager_confirmable`, using
  `--need-clarification manager_confirmable`; `--need-clarification user_required`
  reserves all otherwise undeclared clarifications for the user. Explicit
  phase/task declarations override either overall choice. The Manager may answer
  only when the complete
  answer stays within the confirmed Delivery Contract, its scope, explicit
  constraints and existing authority, and triggers no deviation; otherwise it
  remains user-owned;
- `need_permission`: user required unless the exact permission already exists
  in the current thread;
- `alignment_checkpoint`: manager-resolvable only when the proposal is clearly
  within confirmed strategy and mandate.

A runtime `to_owner=user` baton or `Workflow is waiting for user input` output
is a hard stop unless the confirmed overall clarification policy or an explicit
task declaration authorizes Manager completion. Explicit user ownership wins.

Existing v6 contracts lack the overall policy and retain task-only ownership
until explicit reconfirmation. Existing v5 contracts retain their explicitly
confirmed overall clarification choice. Existing v7 Driver contracts remain
valid through the legacy Driver authority, with their confirmed overall choice
and task overrides. New v8 Manager contracts record both the overall choice
and task overrides; reading old records never inserts the new default or
rewrites digests.

### Delivery facts to confirm

Before rendering, read the request and relevant existing evidence, then propose
one compact version-3 product `delivery_contract` object. Use the user's
language. Keep purpose, scope, and implementation direction separate:

| Field | Content |
| --- | --- |
| `schema_version` | `3` |
| `outcome` | Purpose: the intended result and why it matters |
| `in_scope`, `out_of_scope` | Explicit lists; include required edge cases and integrations |
| `acceptance_invariants` | Concrete completion criteria, without a second evidence checklist |
| `implementation_direction` | Recommended approach; advisory, not a binding method |
| `permissions` | Task-specific action and target authorizations; no implied side effects |
| `constraints` | A flat list of actual fixed limits, not generic quality or architecture boilerplate |

Use explicit empty lists for `out_of_scope`, `permissions`, and `constraints`
when none apply. Purpose, in-scope behavior, completion criteria and recommended
direction must not be empty. Do not infer permission or an external-effect
approval from scope or technical advice. The Manager's standing rule remains:

> The Manager may accept a requirement-equivalent implementation with a smaller
> or simpler implementation footprint. It must not accept reduced user-visible
> behavior, feature scope, acceptance coverage, edge-case coverage, or required
> integrations.

The formatter adds the separately supplied `--deliver` and `--cleanup` commands
to the version-3 product core as `closeout_plan`; do not put that field in
`--delivery-contract` as well:

| Field | Content |
| --- | --- |
| `deliver` | Ordered, user-confirmed objects shaped as `{ "argv": ["literal", "arguments"] }` |
| `cleanup` | Ordered, user-confirmed objects shaped as `{ "argv": ["literal", "arguments"] }` |

The complete confirmed plan is action-specific authority for these exact
commands only. It does not authorize an argument change, target/effect change,
or unrelated external action.

Keep the contract specific about the result and flexible about implementation.
Only user-fixed requirements and applicable safety, permission, compatibility,
or external-effect boundaries are hard limits. If the user fixes a particular
method, name it in `constraints`; otherwise the implementation direction is a
recommendation, not a reason to stop an equivalent approach. Do not create
separate variations, deviation-trigger, quality, cost or evidence sections.

A later technical clarification within the confirmed scope and constraints updates
ordinary phase feedback or artifacts only. It does not replace the Manager
contract, require kickoff reconfirmation, or justify archiving, deleting, or
rebuilding callback dispatch state. Reconfirm only when the user-visible
outcome or scope, authority, external side effects, or an explicitly fixed
invariant materially changes.

Render these facts with the complete kickoff, resolve material ambiguity, and
interpret the user's response semantically in any language. Acknowledgement of
one part does not confirm unreviewed facts. Retain existing explicit decisions;
do not repeatedly ask for unchanged choices. Only the confirmed facts become
`delivery_contract` in the single schema-version-8 durable Manager contract. The
nested Delivery Contract has its own version; no feature-specific sidecar is authority.

Inspect the selected effective entry point, transitions, `initial_input`,
`input_artifacts`, and `output_artifact` declarations. Supply the confirmed
product facts through the entry step's existing initial-input provider/binding
and ordinary input interfaces where needed. Pass outcome/scope/acceptance and
implementation facts, not Manager authority instructions or the policy JSON.
Preserve the original issue input as well. When no such input is declared, use
only an existing supported input boundary; do not synthesize a phase, artifact,
or gate. If required facts cannot be conveyed, raise the existing clarification
handoff. Specification/planning outputs, when present, may refine these facts
but cannot silently reduce or extend them. The same rule applies to diagnosis,
development, drafting, research, or any other entry step.

### Render the proposal

Use the bundled formatter instead of a prose-only summary. Its stdout is a
self-contained initial confirmation request: present the complete output so the
user sees every field being confirmed, including `deliver` and `cleanup`. Do not
substitute a shorter hand-written recap.

Use the rendered document as the response body, preserving its section order and
already-localized facts and action descriptions verbatim. Translate only the
presentation text that is not yet in the effective conversation language; do not
rewrite already-localized scope, acceptance, constraints or explanations into a
second version. The translation rules below still apply to every remaining label
and fixed sentence. Put task-specific limitations in the delivery facts before
rendering; add only an actionable warning absent from the rendered document.
Review the document once for completeness and exact commands, then present it
without a separate introduction, model-rationale table or closing recap.

Render the descriptive delivery facts as separate subheadings with bullet
points, not a two-column table with long cells or HTML line breaks. Keep purpose,
scope, and implementation direction separate. Preserve literal-text escaping
during translation so fact content cannot introduce new Markdown sections.
Compact execution settings,
model chains and gates may stay in tables. Notification/session
mechanics follow the selected Manager mode; do not add a separate notification
field or another approval choice for them.

Present `deliver` and `cleanup` as separate subheadings with ordered actions.
Each action has a concise explanation in the conversation language and its
complete, copyable command in a code block. Supply one `--deliver-description`
or `--cleanup-description` per command, in the same order; provide none for an
empty stage. Describe the actual action and target, including destructive
effects, rather than a vague "clean up resources". The Manager writes these
explanations from context; the formatter does not classify command names.

Descriptions are presentation only, not new contract fields or authority.
Keep every command and its order visible, never replace it with its description.
The formatter shell-quotes the stored argv for display; execution still passes
the original argv directly, never the rendered shell string. Preserve the code
blocks and their quoting during translation: do not replace ASCII quotes or
hyphens with typographic punctuation. Empty stages show that no command runs.

Translate all presentation text into the effective conversation language:
headings, readable field labels, descriptions, capability prompts and outcomes,
authority explanations, and the confirmation request. The Manager owns this
translation, including free-form text from manifests or repository context;
the formatter's source language or English fallback is not the response language.
This applies to any conversation locale, without requiring a translation catalog.

Preserve literal commands and argv arrays, paths, URLs, CLI/model names, playbook
and step IDs, setting keys, and typed values. Add a localized explanation beside
an unfamiliar policy token when needed, without changing the token. Translate
every requirement and limit faithfully; do not summarize, omit, change gate
ownership, or broaden permission. Translation is presentation-only: activate the
same validated proposal, not a translated copy of the saved policy. Keep the
single final diagram's structure and facts as specified in `workflow_progress.md`.

Complete means all user decisions are visible once: product scope and acceptance,
constraints, ordered model chains, confirmation ownership, authority boundaries,
locales, checkout, publication, and exact closeout commands. Keep the saved
policy, duplicate semantic projections, schema versions, source paths, tokens,
digests, timestamps, model-selection diagnostics, and execution-profile matrix
out of the conversation. Worktree and command paths remain visible because they
identify the user's approved targets. Show check results and actionable failures
only when they require attention. Read repository mandate as context; carry only
applicable task-specific permissions and fixed limits into the compact contract,
not the mandate table, preset, axes or grounds. Do not add a second
confirmation prompt or repeat the reason for requesting confirmation after the
formatter output. Ordinary follow-up discussion may be concise.

## After confirmation or direct formatter use

Continue with `kickoff_execution.md` for the direct formatter CLI example,
preparation checklist, durable activation and attached execution polling. Read
the applicable section before that operation. Preparing a proposal through the
consolidated helper requires none of those execution examples.
