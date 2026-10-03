# Reusable kickoff inputs

`prepare_kickoff.py` discovers reusable evidence, assembles one editable request,
and passes completed inputs to the existing kickoff formatter. It does not
activate a workflow or execute proposed delivery actions.

## Prepare a draft

Create the draft directly; no handwritten request JSON is needed:

```sh
python scripts/prepare_kickoff.py draft --issue-id 123 --playbook-id standard-qa --output draft.json
```

Use `--issue-name` for an issue without a numeric ID. The current directory is
the project root unless `--project-root` is supplied. Pass `--manager-cli` when
the caller cannot be identified from its session, and repeated `--phase-chain
phase=cli:model` only for explicit model overrides. Configured values need no
manual transcription. The command refuses to overwrite an existing draft.

Read `draft.json` and fill the unresolved fields in place. Keep the computed
worktree, mode, actions and configured values unless the current request calls
for an exception. Product fields, `phase_chain` and `capability_choice` use their
actual names and types in the generated draft; do not reconstruct this schema.
If the playbook is not selected, omit `--playbook-id`, apply
`playbook_selection.md` to the returned candidates, then set `playbook_id` and
reassemble to populate that playbook's defaults:

```sh
python scripts/prepare_kickoff.py assemble --request-file draft.json --summary --draft-output updated-draft.json
```

Use inherited preference/evidence directories throughout preparation. Temporary
proposal files do not require empty stores. Explicit `--config-dir` and
`--cache-dir` select isolated stores without fallback. The optional `stores`
command returns the effective paths and a stage command with those paths pinned.

The program writes known values into `draft.json`, rather than asking the caller
to copy them from the discovery report:

- request identity and explicit current inputs;
- saved conversation language and Manager mode, plus the explicitly saved
  `manager.poll_interval_seconds` or `manager.event_manager` preference when
  applicable to that mode;
- `event-driven` when no mode is specified or saved; the current caller's CLI
  supplies the event Manager, not a phase model or a new provider choice;
- `.cafe/worktrees/issue<id>` in the main repository, or the explicitly named
  issue's equivalent path; non-Git first tasks use the current checkout;
- cleanup: close the explicitly identified GitHub issue in the current repository
  when a GitHub remote is available, then `cafe close`; without that binding,
  propose only `cafe close`;
- `deliver` and its descriptions from a validated cached `delivery_template`,
  expanding current issue/worktree values into literal argv;
- repository content language through the existing strategic-context resolver;
- conversation language through the existing workflow/playbook locale owner
  when no current preference was supplied;
- configured model chains for omitted phases, preserving explicit overrides;
- existing formatter defaults for confirmation ownership, proactive review,
  permission, clarification and alignment policies;
- empty description lists for explicitly empty action plans, and original
  check report payloads supplied by file reference.

The response's `prefilled` map identifies configuration/default sources. These
are editable proposal values, not a confirmed contract. Model suitability still
requires assessment. Missing or invalid configuration is reported instead of
inventing a model or replacing the user's value. Cached narrative conventions
alone are not executable templates. Store a structured delivery template once
the repository route is established; source changes invalidate it normally.

The caller still supplies the current product outcome, scope, acceptance,
implementation direction, constraints and permission boundaries; chooses the
playbook when unspecified; resolves a missing/stale delivery template and any
non-default mode parameters; and makes capability/preflight decisions. Review
prefilled choices, concrete targets and requested exceptions. The complete
proposal still requires confirmation before creating a worktree or executing
delivery and cleanup.
No raw documentation needs rereading solely to transcribe a configured value.

Assembly returns the selected graph, candidate applicability, evidence and its
provenance, the formatter's complete input schema, and missing inputs. Exit 3
means preparation is incomplete. The draft is the editable request used by all
subsequent commands. There is no separate decision view, source-reference index
or generated policy reading plan. Read applicable policy from its owner:
`kickoff.md`, `model_selection.md` and `strategic_context.md`.

Put current named choices in `current_explicit_inputs` and assessed decisions
in `formatter_inputs`; conflicting duplicates are rejected. Supplied false and
empty values are preserved. Product fields use the existing `DeliveryContractV3`
schema. `implementation_direction` is a string; list fields are arrays.
`closeout_plan` is built by the formatter. An unresolved delivery route remains
null, not an automatic empty array. A null draft action slot may receive a
valid template/default later; an explicit `[]` excludes that stage and wins over
the defaults. Deliberate `deliver`/`cleanup` choices are literal
argv arrays, with one description per action. `schema` exposes all field types
and the existing parser's allowed values without conditional filtering.
An unknown or invalid field blocks rendering but preserves the editable values
and computed defaults in the draft. Correct the reported field; do not discard
the remaining draft or silently drop the error to render.

Delivery and model cache hits provide evidence, not action authority or proof
that a model suits the current issue. Inspect missing, stale or contradictory
sources; reuse valid facts for the conventions they cover. Saved preferences
apply only to new proposals. An existing workflow keeps its confirmed contract.

## Offer to remember preferences

During new kickoff preparation, use the supported proposal preference keys in
`kickoff_input_reference.md` and their applicability to the selected graph and
Manager mode. These prompts and saved choices are project-specific: use
`--scope repository --project-root <current-project-root>` with the same config
directory for inspection and storage. Reuse reported repository preference
records; inspect that scope when a key's storage status is not shown. A prefilled
configuration, policy default or inherited user preference does not count as a
saved project preference. A malformed or incompatible repository record needs
correction, not treatment as an absent value.

For preferences with independently configurable entries, check coverage per
applicable entry, not just whether the top-level key exists. In `phase.chains`,
a saved step selector covers that step; otherwise its saved role selector may
cover it. In `review.decisions`, inspect each applicable step. Use the existing
consumer's applicability and precedence rules; a partially saved map does not
make its uncovered entries saved preferences.

- For applicable keys or entries with no saved repository preference, ask whether the
  user wants to remember the proposed values for future kickoffs in this project.
  Group these into one concise question alongside contract confirmation, listing the values
  and the project they apply to. Do not offer or write user-wide preferences
  in this flow. For unresolved values, combine the reuse
  question with the existing request for that decision; do not invent a value
  just to save it.
- When a user explicitly chooses a value different from what would otherwise
  apply (saved preference, repository configuration or policy default), show the
  old value/source and the current choice, then add: "This applies to this
  kickoff. Tell me if you want it saved for future kickoffs in this project."
  This also applies when a saved preference already exists. If the same key is in the unset
  preference question, combine the reminder there instead of asking twice.
- Ask once per proposed key/value/scope during this preparation. An explicit
  request to remember it already answers the question. A decline or unanswered
  save question leaves storage unchanged; ordinary contract confirmation is
  not consent to save. Keep using the current proposal without adding a
  separate workflow gate for optional preference storage.
- Before saving a partial map change, inspect the current repository record and
  preserve its other entries: `preferences set` replaces the whole value for a
  key. Merge only the agreed entries into that record, without copying inherited
  user preferences into project storage. Validate the resulting shape using the
  existing consumer rules. Do not merge coupled arrays such as action/description
  lists or gate partitions independently; show and obtain consent for their
  complete replacement value when a change affects both.
- After explicit reuse consent, run `preferences set --reuse --origin explicit
  --scope repository --project-root <current-project-root>` for only the agreed
  keys and values, then inspect repository scope to verify the stored result and
  report what was remembered. Follow `kickoff_input_reference.md` for commands
  and reusable value shapes. Issue-specific targets, permissions, capability
  grants, evidence and model suitability judgments are not preference choices.
  Inferred values require explicit user adoption before saving; action/worktree
  conventions use reusable templates, not this issue's literal targets.

## Complete checks and render

The response's `continuation` identifies missing checks and capture commands
using the same draft. Run each required check under `project_global_skill_sync.md`
and capture its complete original JSON with the actual observation timestamp:

```sh
observed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
set -o pipefail
cafe update check --json | python scripts/prepare_kickoff.py capture-report --request-file draft.json --kind update --report-output update.json --checked-at "$observed_at"
observed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
python scripts/catalog_version_check.py | python scripts/prepare_kickoff.py capture-report --request-file draft.json --kind catalog --report-output catalog.json --checked-at "$observed_at"
```

Resolve script paths from the installed skill directory. Capture only records
original report bytes and the supplied observation time; it does not execute
checks or approve a disposition. Parallel captures preserve both references.
Inspect the reports and resolve `decision` and `post_change_evidence` in
`preflight_metadata`. Existing report files can instead be supplied through
`preflight_files`; preserve their complete tokens, diagnostics and digests.
Do not invent successful reports or timestamps. Explicitly unavailable values
may remain null where the existing owner permits them.

After resolving the draft:

```sh
python scripts/prepare_kickoff.py render --request-file draft.json --output proposal.md
```

The command uses the existing formatter, writes the complete proposal and returns
a file receipt. Failure preserves an existing output file and reports unresolved
inputs. Without `--output`, the rendered text is at `render.output`. Present the
complete formatter output for confirmation. A manually written draft is not a
completed kickoff. No workflow or delivery action runs before confirmation.

## Maintenance

Use `preferences inspect/set/clear` for explicitly reusable user or repository
preferences. Do not save one-off choices or authorization as preferences.
Use `evidence inspect/refresh/clear` for source-backed catalog, delivery and model
records; an operational probe does not establish model capability. See
`kickoff_input_reference.md` for evidence formats and maintenance commands.
