# Reusable kickoff inputs

`prepare_kickoff.py` gathers local preferences and evidence in two stages, then maps an explicit complete decision set into the existing kickoff formatter. It reports missing research and decisions; it does not choose a playbook, infer issue acceptance criteria, determine model suitability, or invent delivery commands.

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
