# Reusable kickoff inputs

`prepare_kickoff.py` gathers local preferences and evidence in two stages, then maps an explicit complete decision set into the existing kickoff formatter. It reports missing research and decisions; it does not choose a playbook, infer issue acceptance criteria, determine model suitability, or invent delivery commands.

The normal new-issue Manager path is `discover` → assessment → `assemble` →
`render` when selection is open. Start with a summary before broad source
reading: `discover --summary` when selection is open, or `assemble --summary decisions`
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
python scripts/prepare_kickoff.py stores --request-file request.json --summary decisions
```

It returns the effective `storage` paths and repository identity, plus a literal
`next_command` argv for the selected stage with those paths pinned. Execute that
argv; for selected assembly append `--draft-output draft.json`. Later use the returned `render_command` and append `--output
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
python scripts/prepare_kickoff.py assemble --request-file request.json --summary decisions --draft-output draft.json
```

The first selected assembly writes the editable `draft.json` and returns a
`decision_brief`, directly readable on stdout; consume this response once without
a separate pretty-print or key-reprint command. Add the verbatim request as `request_text`; current named choices
belong in `current_explicit_inputs`. Start with these current-response fields:

- `fixed_inputs`: supplied choices or applicable preferences and their origin;
- `questions`: current scope, model suitability, actions/authority, gates, locale,
  preflight and presentation judgments, including available evidence and actual
  evidence gaps. These judgments remain necessary even when fields are complete;
- `missing_fields`: unresolved fields referencing one `question_id` and one
  `field_reference`; resolve both within this response instead of opening schema
  or implementation files;
- `reading_list`: one source index with short section IDs, file fingerprints and
  disjoint line ranges. Each question references those IDs. For the judgment at
  hand, use `read_command_template` with its source path and section line range;
  reuse that section for later questions that reference the same ID. This is not
  a command to concatenate the entire source union before considering the facts;
- `repository_reading_candidates` and `current_mandate_path`: current strategy
  sources from the existing generic resolver. Inspect the mandate and applicable
  grounds once, sharing the same observations across scope, models and delivery.
  Missing or ambiguous grounds remain decisions, not permission to skip them;
- `workload_evidence`: literal coverage references, not an assignment, equivalence
  ranking or proof of suitability. Assess reasoning, risks, limitations and exact
  chosen-chain identities against the full valid payloads already in the response;
- `field_shapes`: the single existing-owner type/contract index for editing the
  draft, limited to unresolved fields and their owner constraints. Known choices
  retain their provenance; blank product values and empty action arrays still
  require current judgment;
- top-level `source_index`: complete provenance records. Delivery/model `sources`
  use local JSON `$ref` pointers into this index; resolve them here, without raw
  evidence inspection merely to retrieve the same record;
- top-level `continuation`: when check reports are absent, execute each existing
  `check_argv` once, pipe its complete JSON into the supplied `capture_argv` with
  the actual observation time, and assess the captured report's disposition in
  this same draft. Then use `render_command` with `--output proposal.md`. Missing
  reports block a complete endpoint even when all product decisions are filled.
  Proposal-only excludes proposed case actions; it still requires these checks.

A validated delivery fact satisfies only the convention it actually describes.
An uncovered target, strategic question, changed source or contradiction still
requires inspection. A model hit likewise does not prove issue suitability. Use
these gaps to identify the relevant section or evidence, sharing unchanged reads
across judgments. The original policy text remains authoritative; every listed range has
its actual path, lines and source hash. Changed source invalidates that read plan.

`--guidance-output <guide.md>` and `--with-guidance` remain full owner projections
for explicit inspection/legacy consumers. They are not a prerequisite to this
reading path. Full schema is available through the returned `schema_reference`;
bare `--summary` (also `--summary json`) and full `assemble` retain their
existing data. `--summary decisions` defers unselected role defaults, artifact
plumbing, discovery-only dependencies and unused schema/examples through
`deferred_details`, using the existing discover/schema endpoints. All candidate
applicability/diagnostics, selected profiles/routes/gates, usable evidence and
limits stay visible. Only actual gaps justify opening those details.
With a draft file, the response omits duplicate editable values/schema and points
to that file plus the relevant field shapes. No policy or candidate is silently
removed: selected facts, all candidate diagnostics/overview and detailed inspect
references remain available. Execution/activation sections remain at their linked
owner paths until entering those operations.

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
authority. Read current repository strategy documents as required by the owner,
using the union of paths needed for scope, model suitability and delivery. One
read of an unchanged document serves all three judgments; use sufficient validated
delivery facts for their covered conventions and open only the uncovered strategy
sections. A new contradiction or changed source still requires inspection.

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
`decision_brief.field_shapes.formatter_field_schema` supplies every adapter field's JSON type,
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
is linked by the reading list; read each required section once. Valid model
`assessment` includes workloads, reasoning, capability bands, limitations and
sources; delivery `sources` identifies the evidence supporting its conventions,
separately from the discovery manifest. A delivery hit with no current observations
does not prove remote branch, PR state or current authorization. Resolve such
issue-specific gaps explicitly without repeating unchanged convention research.

Capture each required check's original output on its **first** execution using
`capture-report`; this input adapter does not execute or modify preflight. For
example, after the current policy permits the read-only check:

```sh
observed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
set -o pipefail
cafe update check --json | python scripts/prepare_kickoff.py capture-report --request-file draft.json --kind update --report-output update.json --checked-at "$observed_at"
observed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
python scripts/catalog_version_check.py | python scripts/prepare_kickoff.py capture-report --request-file draft.json --kind catalog --report-output catalog.json --checked-at "$observed_at"
```

Resolve the source script paths from the installed skill directory. Inspect the
saved reports to make the current decisions; fill only `decision` and
`post_change_evidence` in the draft's `preflight_metadata`. Capture serializes updates to the same draft so parallel update/catalog producers
retain both references. It preserves original JSON and actual caller-supplied time, never generates success or a
policy decision. Keep a failing producer's status visible (`pipefail`); data
capture is not successful preflight. Do not rerun a check merely because its
output was not yet wrapped for the formatter. Explicit changes/expiry still
require the normal owner-directed recheck.

After filling the gaps, write the complete proposal once:

```sh
python scripts/prepare_kickoff.py render --request-file draft.json --output proposal.md
```

This returns a compact status/file receipt. Failed rendering preserves any
existing output file and reports `validation_error` plus missing decisions
without dumping the selected graph. Pass the complete existing preflight reports via
`preflight_files`; do not reconstruct a subset and lose comparison tokens.
Raw check command output alone may lack the existing report metadata. Follow
the reading list's Complete runtime and catalog preflight section and the preflight
owner for those decisions; never invent `comparison_token`, `checked_at` or
post-change evidence. Draft output retains file references instead of copying
report payloads. the standalone schema's `preflight_report_examples` shows the complete
required field shapes, not valid check results. Retain extra original fields
such as mismatch IDs. Use the actual observation timestamp/current decision
and source-provided tokens/digests; an explicitly unavailable source value may
remain null, never a made-up token or successful status. These examples do not
change the existing formatter/preflight owners. Read `proposal.md` once and present
it completely. Existing callers without `--output` still receive JSON with text
at `render.output`; do not guess a top-level output field. Use a new issue identity
for a new proposal; an existing identity intentionally retains its workflow locale.

## Detailed formats and maintenance

Read `kickoff_input_reference.md` when inspecting the complete request format,
calling maintenance operations, refreshing evidence or mapping raw check reports
without capture. Normal preparation uses the typed draft, source-backed summary
and commands above; it need not preload those additional examples.
