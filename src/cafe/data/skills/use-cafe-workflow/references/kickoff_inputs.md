# Reusable kickoff inputs

`prepare_kickoff.py` gathers local preferences and evidence in two stages, then maps an explicit complete decision set into the existing kickoff formatter. It reports missing research and decisions; it does not choose a playbook, infer issue acceptance criteria, determine model suitability, or invent delivery commands.

The normal new-issue Manager path is `discover` → assessment → `assemble` →
`render` when selection is open. Start with a summary before broad source
reading: `discover --summary` when selection is open, or `assemble --summary`
when the user already chose a graph, even before formatter decisions are complete.
Then assess current scope/strategy/suitability/authority, fill the reported gaps,
and `assemble` → `render`. Use one request file and retain the effective preference/evidence directories
throughout preparation and its follow-ups. A warm session is a fresh Manager context that reads the saved
records; it must still assess the current issue and validate source freshness.
For an existing workflow, read its confirmed contract and generic state rather
than applying changed preferences to the issue.

After gathering complete, source-backed delivery conventions or an exact model
capability assessment during the current preparation, persist that Manager
evidence for later warm preparations with `evidence refresh --category delivery`
or `evidence refresh --category models`, respectively. Use the documented
evidence-file format and retain the same isolated cache directory. Refresh only
evidence that the current assessment supports; incomplete research, a discovery
gap, or an operational model probe alone is not reusable evidence. This local
cache update does not confirm a model chain or authorize a delivery action.

## Store selection before preparation

A temporary proposal directory is an output location, not a new preference or
evidence store. For a normal proposal inherit the caller's `XDG_CONFIG_HOME` and
`XDG_CACHE_HOME` (or their home-directory defaults). Do not assign fresh XDG roots
merely because the proposal is read-only or uses temporary request/draft files.
Disposable evidence validation may update its existing cache; it does not modify
saved preferences or authorize any proposed action.

After writing the request, run the read-only locator first:

```sh
python scripts/prepare_kickoff.py stores --request-file request.json
```

It returns the effective `storage` paths and repository identity, plus a literal
`next_command` argv for the selected stage with those paths pinned. Execute that
argv; for selected assembly append `--guidance-output guidance.md --draft-output
draft.json`. Later use the returned `render_command` and append `--output
proposal.md`. These argv values prevent incidental environment changes from
switching stores between stages. They are local invocation inputs, not saved
issue authority or permission. Source/identity/freshness checks still run.

Use `--config-dir` / `--cache-dir` on `stores` or any stage only for an intentional
store choice, such as a user-requested isolated evaluation. Explicit choices
are honored with no fallback to another directory. If changing them deliberately
later, regenerate the stage command from the new choice. Empty stores honestly
miss; a different repository or changed material source cannot borrow a prior hit.
Reports expose effective `storage` so the caller can verify which records it used.
No raw source needs reopening simply to verify a validated hit.

## Early selected-graph request

Create a request in a temporary directory with only the facts already known:

```json
{"schema_version":1,"project_root":"/work/project","issue_name":"new-issue","playbook_id":"standard-qa","current_explicit_inputs":{"effective_locale":"zh-TW","locale_source":"explicit","repository_content_locale":"en-US"}}
```

```sh
python scripts/prepare_kickoff.py assemble --request-file request.json --summary --guidance-output guidance.md --draft-output draft.json
```

The first selected assembly returns a decision brief, owner-derived nested
`input_schema`, and an editable `draft.json`. Add the verbatim request as
`request_text`; current named choices belong in `current_explicit_inputs`.
Read this response before opening other kickoff references. `--guidance-output`
projects the current normative owner sections, with file hashes, including
strategy, model suitability, locale, action authority, gates, preflight and
presentation into a plain-text file. `guidance_index` gives disjoint line ranges
and source hashes. Read each applicable range once; do not print the entire
assembly JSON and then reprint its guidance. These are the original sections,
not another policy owner. `--with-guidance` remains an optional embedded JSON
form for consumers that need it, not the normal conversational reading path.
Apply them to this issue once. They replace rereading those same sections;
load omitted execution/activation sections only when entering that operation.
Subsequent assembly calls omit both guidance options.

Edit `draft.json` directly. The product skeleton is generated from
`DeliveryContractV3`: `implementation_direction` is a string, list fields are
arrays, and `closeout_plan` is absent because the formatter constructs it.
Blank product values are unfinished decisions. `deliver: null` and
`cleanup: null` are unresolved slots; replace each with deliberate literal argv
arrays, including `[]` only when justified by the current decision. They never
become automatic empty action plans. When supplying action descriptions, use
`deliver_description` and `cleanup_description` as string arrays, with exactly
one nonempty explanation per command. For `cleanup: []`, use
`cleanup_description: []`; put the explanation for retaining resources in the
product constraints, not in a description for a nonexistent command. The public
`action_input_examples` covers both cases and assembly checks them through the
existing formatter owner before final render. The public schema includes lifecycle
examples checked by the existing closeout validator: `cafe close --archive-only`
is not a valid closeout-plan command. No source-code inspection or trial render
is needed to learn these shapes. `schema` remains available independently.

`decision_brief` links the current judgment to the selected graph and validated
evidence already in this response. Read hit assessment payloads, source dates,
limits and provenance here; do not reopen raw records merely to verify the hit.
Inspect the named source only for an actual uncovered workload, target,
contradiction or invalidation. The brief does not decide scope, suitability or
authority. Read current repository strategy documents as required by the owner.

An incomplete assembly (exit 3) is expected at this stage. Its `formatter_draft`
prefills request identity, explicit fields and applicable preferences, while
`missing_decisions` names what remains. `schema` lists the accepted fields and
examples without reading implementation code or doing discovery. Keep one
request; put already confirmed named fields in `current_explicit_inputs`, and
newly assessed decisions in `formatter_inputs`. Conflicting duplicates are
rejected. Lists such as `phase_chain` contain `phase=provider:model` strings;
`capability_choice` contains `name=true|false` strings; `deliver` and `cleanup`
contain literal argv arrays. Reasoning effort is a separate confirmed execution
setting; never append it to a model ID (for example, `@medium` would become
part of the literal model identity). No action, model chain or contract is synthesized.
`input_schema.formatter_field_schema` supplies every adapter field's JSON type,
array item shape and parser-owned choices (including reactive policies and Manager
mode). Use these fields directly; no formatter source or argparse lookup is needed
to learn input types or legal values. This projection shares the adapter encoding
maps and current formatter parser, so it cannot introduce another set of choices.
Fields absent from this schema, such as a phase reasoning-effort override, are not
accepted formatter inputs; keep those current execution decisions in the complete
proposal under their existing owner. The existing formatter still validates the
complete decision set.

Selected assembly exposes the chosen graph and all invalid-candidate diagnostics,
plus `catalog.candidate_overview`: every effective candidate's declared applicability,
roles, step IDs, eligibility and source fingerprint. Use this overview to inspect
candidates even when the graph was already explicitly selected; do not reopen their
YAML or rerun list/show merely to recover that comparison. The selected graph retains
its complete decision facts; the inspect reference retains every other candidate's
full details for a specific uncovered question. Current playbook-selection policy
is included in the owner guidance projection, so its source need not be read again. Valid model
`assessment` includes workloads, reasoning, capability bands, limitations and
sources; delivery `sources` identifies the evidence supporting its conventions,
separately from the discovery manifest. A delivery hit with no current observations
does not prove remote branch, PR state or current authorization. Resolve such
issue-specific gaps explicitly without repeating unchanged convention research.

After filling the gaps, write the complete proposal once:

```sh
python scripts/prepare_kickoff.py render --request-file draft.json --output proposal.md
```

This returns a compact status/file receipt. Failed rendering preserves any
existing output file and reports `validation_error` plus missing decisions
without dumping the selected graph. Pass the complete existing formatter-ready preflight reports via
`preflight_files`; do not reconstruct a subset and lose comparison tokens.
Raw check command output alone may lack the existing report metadata. Follow
`guidance`'s Complete runtime and catalog preflight section and the preflight
owner for those decisions; never invent `comparison_token`, `checked_at` or
post-change evidence. Draft output retains file references instead of copying
report payloads. `input_schema.preflight_report_examples` shows the complete
required field shapes, not valid check results. Retain extra original fields
such as mismatch IDs. Use the actual observation timestamp/current decision
and source-provided tokens/digests; an explicitly unavailable source value may
remain null, never a made-up token or successful status. These examples do not
change the existing formatter/preflight owners. Read `proposal.md` once and present
it completely. Existing callers without `--output` still receive JSON with text
at `render.output`; do not guess a top-level output field. Use a new issue identity
for a new proposal; an existing identity intentionally retains its workflow locale.

## Request file

A request is UTF-8 JSON with `schema_version: 1`, `project_root`, and `issue_name`. Optional fields include `playbook_id`, `current_explicit_inputs`, `manager_decisions`, `required_decisions`, `delivery_evidence`, `model_assessments`, `current_model_sources`, `model_contradictions`, `preflight_files`, and normalized `formatter_inputs`.

`formatter_inputs` uses named JSON fields that map to the existing formatter. For example:

```json
{
  "schema_version": 1,
  "project_root": "/work/project",
  "issue_name": "issue600",
  "playbook_id": "standard",
  "manager_decisions": {
    "assessment": "A bounded implementation with confirmed repository scope."
  },
  "formatter_inputs": {
    "playbook_id": "standard",
    "issue_name": "issue600",
    "delivery_contract": {"schema_version": 3},
    "deliver": [],
    "cleanup": [],
    "update_preflight": {"status": "current"},
    "catalog_preflight": {"status": "identical"},
    "repository_content_locale": "en-US",
    "current_checkout": true
  }
}
```

An absent value remains missing. An explicitly empty `deliver` or `cleanup` list and an explicit `false` capability choice remain distinct values. The helper accepts only the declared formatter fields, encodes each value as a JSON or argv element, and rejects activation metadata, shell commands, and unknown fields.

## Staged commands

Run from the installed CAFE Python environment or the source tree:

```sh
python scripts/prepare_kickoff.py discover --request-file request.json
python scripts/prepare_kickoff.py assemble --request-file request.json
python scripts/prepare_kickoff.py render --request-file request.json
```

`discover` reports applicable preferences, every effective playbook candidate and its diagnostics, delivery discovery status, and requested model evidence. It does not select a candidate. `assemble` checks the explicitly selected graph and reports missing decisions with their owner. `render` requires a complete normalized input set and calls the existing formatter in memory. It never activates a workflow or executes delivery or cleanup argv.

For normal Manager guidance, prefer the compact machine-readable views after
the initial scope and authority decisions have been made:

```sh
python scripts/prepare_kickoff.py discover --request-file request.json --summary
python scripts/prepare_kickoff.py assemble --request-file request.json --summary
```

In command notation these are `discover --request-file <request.json>
--summary` and `assemble --request-file <request.json> --summary`; replace the
placeholder with the actual request-file path when running them.

Compact discovery still lists every candidate with its applicability, roles,
profiles, phase routes, confirmation gates, capability requirements and
diagnostics. It includes candidate counts, invalid-candidate diagnostics,
delivery status and source fingerprints, model assessment status and source
provenance with ages, plus commands to inspect the full records. Compact
assembly presents the selected graph facts needed for review, candidate and
invalid-diagnostic counts, missing decisions, and the exact normalized
`formatter_inputs`. The report keeps all data inspectable: full candidate and
diagnostic details remain available through the inspection commands in the report or by running the same command
without `--summary`; `render` continues to produce the complete existing
proposal.

Treat a hit as reusable only while repository identity, source fingerprints,
freshness and applicability validate. Do not repeat unchanged source
inspection after a validated hit, but do perform the first issue-scope,
strategy, action-target and authority judgments for each new preparation. A
hit never supplies those decisions or confirms activation.

During one preparation, keep using the validated compact report as the source
for unchanged discovery facts. Do not dump the full cached discovery or
assembly report again, or reread whole guidance/source files only to reconfirm
facts already present in that report. Reopen the full report only to resolve a
specific missing decision, diagnostic or invalidated input, and read only the
relevant detail.

Preflight reports may be passed as JSON objects in `formatter_inputs`, or by path in `preflight_files.update` and `preflight_files.catalog`. The helper reads those files as data; it does not execute their contents.

## Preferences

Preferences are versioned local records kept apart from disposable evidence:

- User preferences: `${XDG_CONFIG_HOME:-~/.config}/cafe/kickoff/preferences-v1.json`
- Repository preferences: the same configuration root under `repositories/`, keyed by canonical repository identity.

Effective order is current explicit input, applicable repository preference, user preference, then policy default. A one-off explicit value is not persisted. Saving requires explicit reuse intent:

```sh
python scripts/prepare_kickoff.py preferences set \
  --scope repository --project-root /work/project \
  --key manager.mode --value-json '"unattended"' --origin explicit --reuse
python scripts/prepare_kickoff.py preferences inspect \
  --scope repository --project-root /work/project
python scripts/prepare_kickoff.py preferences clear \
  --scope repository --project-root /work/project --key manager.mode
```

`--origin inferred` and values representing action authorization or a concrete issue target are rejected. Clearing one key reveals the lower-priority applicable value and leaves other keys and scopes intact. Reads of a corrupt or unsupported store return no preference hits; they do not rewrite it. A later explicit set uses atomic replacement. A failed write preserves the last committed file.

Language values retain their scope and whether they were explicit or inferred. The locale adapter still supplies the selected value to the existing generic creation boundary. A saved preference does not change the language, contract, or pending tasks of an existing workflow.

## Evidence and freshness

Evidence lives under `${XDG_CACHE_HOME:-~/.cache}/cafe/kickoff/v1/`. It is separate from preferences and confirmed workflow state.

- Delivery observations expire 24 hours after observation, or at an earlier source validity bound. Stable local conventions can remain reusable while their source content and discovery context match.
- Exact model assessments expire seven days from the older of the assessment time and earliest supporting-source retrieval, capped by earlier source validity.
- Model evidence requires a provider, exact non-floating model version, primary source URLs and fingerprints, dates, assessed workloads/reasoning, capability bands and stated limitations.
- Operational availability/fallback probes are not model capability evidence. Current contradictory evidence, changed source fingerprints, changed repository identity, changed material paths, invalid dates, or expired evidence make the affected item unresolved.
- Delivery discovery retains tracked and non-ignored file-name membership and candidate documentation/configuration/script sources. A changed source or a new path outside classified coverage leaves a discovery gap. Editing the contents of an already-known unrelated source file does not invalidate unchanged delivery facts.

Inspect, refresh and clear one category at a time:

```sh
python scripts/prepare_kickoff.py evidence inspect --category catalog
python scripts/prepare_kickoff.py evidence refresh --category catalog --request-file request.json
python scripts/prepare_kickoff.py evidence clear --category catalog

python scripts/prepare_kickoff.py evidence inspect --category delivery --project-root /work/project
python scripts/prepare_kickoff.py evidence refresh --category delivery --project-root /work/project --evidence-file delivery.json
python scripts/prepare_kickoff.py evidence clear --category delivery --project-root /work/project

python scripts/prepare_kickoff.py evidence inspect --category models
python scripts/prepare_kickoff.py evidence refresh --category models --evidence-file model-assessment.json
python scripts/prepare_kickoff.py evidence clear --category models
```

Catalog refresh derives current effective declarations. Delivery and model refresh require Manager-gathered evidence files; failed or incomplete refresh does not extend an older record. `inspect` reports the stored evidence without renewing it. `clear` affects only the chosen category/key. After clearing or encountering corruption, the next discovery is honestly cold and reports any evidence or decisions that must be gathered again.

These records are preparation facts only. They do not authorize publication, issue changes, workflow activation, paid services, or exact delivery/cleanup actions. The complete formatter output and existing confirmation/activation boundaries remain required.
