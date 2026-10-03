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

Read `draft.json` and fill unresolved fields in `formatter_inputs` in place.
Make intentional overrides in those same fields, without creating a second input
map. Keep the computed
worktree, mode, actions and configured values unless the current request calls
for an exception. Product fields, `phase_chain` and `capability_choice` use their
actual names and types in the generated draft; do not reconstruct this schema.
If the playbook is not selected, omit `--playbook-id`, apply
`playbook_selection.md` to the returned candidates, then set `playbook_id` and
reassemble to populate that playbook's defaults:

```sh
python scripts/prepare_kickoff.py assemble --request-file draft.json --summary --draft-output updated-draft.json
```

Continue all edits, report captures and rendering with `updated-draft.json`;
it replaces `draft.json` as the working request in the commands below.

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

Use the existing named fields in `formatter_inputs` for both current choices and
assessed decisions. For conversation language, set `effective_locale` and
`locale_source` together: `explicit` for a user choice, `inferred` for an inference.
Do not retain a generated playbook source when changing the language. Preserve
`generated_inputs` provenance and `preflight_files` references; removing them
does not repair stale evidence. Supplied false and empty values are preserved.
An empty `phase_chain` requests no per-phase overrides; assembly fills configured
chains for the selected graph. Product fields use the existing `DeliveryContractV3`
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

The formatter owns the optional project-preference section and the single
confirmation prompt. Preserve both in the final user-visible contract; an
intermediate asynchronous question is not a substitute. Do not append a second
confirmation question. The section lists applicable missing or changed entries,
groups identical model chains without losing fallback order, and shows saved
values beside current values. Identical project preferences are omitted. User
preferences may supply proposal values but do not count as saved project choices.

The final replies have separate meanings:

- "Confirm" / "確認": approve this kickoff only; do not save preferences.
- "Confirm and remember" / "確認並記住": approve kickoff and save only the entries
  displayed in that final contract for this project.
- A scoped reply such as "確認，只記住模型" saves only the selected displayed
  entries. Resolve an ambiguous save selection without blocking an otherwise
  explicit kickoff approval. Never interpret silence as permission to save.

`render --output` returns `preference_offer_file` and a pinned `remember_command`.
Retain that offer: it is the exact displayed collection, separate from the
workflow contract. After explicit reuse consent, run the command with repeated
`--select <entry-id>` for the agreed entries, or `--select '*'` only for all
displayed entries. `--reuse` records the user's reuse decision; the Manager may
not infer it from ordinary confirmation. Without `--output`, the same snapshot
is in `render.preference_offer`; save those exact bytes as JSON before applying.
Do not rebuild the offer after the answer and silently save a different set.
Re-rendered changes must be shown before treating them as approved for storage.

The remember operation always uses repository scope, preserves unrelated
entries, and rejects a selected preference changed since display. A model chain
is one ordered value; action/description pairs and confirmation partitions are
whole values. Inspect the stored repository result and report what was saved.
If the user already explicitly requested reuse, apply the agreed entries without
asking again. Failure to save does not create a workflow gate: report that the
approved workflow can start but the named preferences were not saved.

Only supported reusable settings are offered, never issue targets, capabilities,
permissions or suitability judgments. Worktree and action conventions must use
verified reusable templates. For a route not covered by a saved/validated cache
template or a built-in convention, provide `preference_templates` using the
shapes in `kickoff_input_reference.md`; expansion must match the proposal and
issue-specific names, IDs and paths must use placeholders. Unavailable or invalid
optional preferences are reported in the final output, not silently saved or
turned into an extra kickoff gate. Actual missing/invalid contract inputs still
follow their existing validation. Inferred values need explicit adoption before
being saved. Saving a preference never changes an existing confirmed workflow.

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
