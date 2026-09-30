# Kickoff input schema and maintenance reference

For ordinary preparation start with `kickoff_inputs.md`. This reference retains
the complete request, direct staged examples, preference/evidence maintenance and
raw report formats for callers performing those operations. It introduces no
new schema, authority or freshness owner.

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


### Raw check report files

Keep the original complete update/catalog check JSON in `preflight_files.update`
and `preflight_files.catalog`. For raw CLI reports, supply `preflight_metadata`
with the corresponding `update`/`catalog` objects, each containing exactly the
actual `checked_at`, current Manager `decision`, and `post_change_evidence`.
The helper maps update `token` to `comparison_token` and projects `catalog_check`
while preserving the complete source report. Existing formatter-ready files
continue to work without metadata. Missing actual timestamps and conflicting or
extra metadata are gaps, never synthesized evidence. `input_schema.preflight_file_adapter`
describes this public mapping. An explicit `post_change_evidence: null` may represent absent post-change evidence
under the existing formatter contract; do not invent text to replace it. The
current Manager `decision` must still be supplied. The formatter owns validation; the helper does
not execute or change preflight checks. No manual report reconstruction or
formatter implementation lookup is needed.


Explicit delivery source references may include repository lifecycle or hook files
outside the automatic discovery patterns. They must be in the current repository
inventory, remain inside that repository after path resolution, and match the
supplied fingerprint. Refresh and assessment track those explicit dependencies;
editing one invalidates the record while editing an unrelated implementation file
does not. This adds no action permission or issue-specific decision to stable facts.
