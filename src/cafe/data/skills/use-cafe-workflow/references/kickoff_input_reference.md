# Kickoff input schema and maintenance reference

For ordinary preparation start with `kickoff_inputs.md`. This reference retains
the complete request, direct staged examples, preference/evidence maintenance and
raw report formats for callers performing those operations. It introduces no
new schema, authority or freshness owner.

## Request file

The normal entry point is `prepare_kickoff.py draft --issue-id <id> --output
<draft.json>`, with optional `--project-root`, `--playbook-id`, `--manager-cli`
and repeated `--phase-chain`. Use `--issue-name` instead for a nonnumeric local
identity. The program creates and prefills this request; edit gaps or intentional
overrides in its existing `formatter_inputs` fields. After `assemble --draft-output`,
use the updated draft for subsequent edits, report captures and rendering.
Existing output files are not overwritten. The schema
below is for inspecting or integrating requests, not a requirement to author
a starter JSON object by hand.

A request is UTF-8 JSON with `schema_version: 1`, `project_root`, and either `issue_name` or a positive numeric `issue_id` (which supplies `issue<id>`). Optional fields include `playbook_id`, `manager_cli`, `current_explicit_inputs`, `manager_decisions`, `required_decisions`, `delivery_evidence`, `model_assessments`, `current_model_sources`, `model_contradictions`, `preflight_files`, and normalized `formatter_inputs`.

`manager_cli` identifies the calling Manager, not a phase model. In a Codex
session its existing `CODEX_THREAD_ID` context also identifies that caller.
Without a known caller or saved event chain, the event Manager remains unresolved;
the helper does not choose another provider. Explicit inputs override saved
preferences, which override proposal defaults. Issue-like names are never parsed
as GitHub IDs. Only a supplied `issue_id` and the current GitHub repository can
produce a default `gh issue close <id> --repo <repository>` proposal.

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

An absent value is filled only by the documented preference, configuration,
template or default rules; otherwise it remains missing. An explicitly empty
`deliver` or `cleanup` list and an explicit `false` capability choice remain
distinct values. The helper accepts only the declared formatter fields, encodes
each value as a JSON or argv element, and rejects activation metadata, shell
commands, and unknown fields.

### Legacy and advanced explicit inputs

Normal generated drafts use `formatter_inputs` for current choices and assessed
decisions alike. Set `effective_locale` with its accurate `locale_source`
(`explicit` for a user choice, `inferred` for an inference). Preserve
`generated_inputs` and preflight references when editing.

`current_explicit_inputs` remains an adapter for legacy callers and deliberate
same-value reassessment of invalidated generated values. It is not a second map
to populate during ordinary draft completion. Conflicting duplicate fields in
the two maps are rejected; remove the affected `formatter_inputs` entry when
supplying it through this adapter. Explicit null in this map remains an unresolved
current decision rather than permission to use a generated default.

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

Normal `render` includes project-only save choices in the final contract and
returns the exact `preference_offer`. With `--output`, it also writes a
content-addressed offer JSON alongside the proposal and returns its path as
`preference_offer_file`. This artifact is not part of the confirmed workflow
contract and rendering never writes preferences.

After the user explicitly chooses to remember the displayed entries:

```sh
python scripts/prepare_kickoff.py preferences remember \
  --offer-file <displayed-offer.json> --project-root /work/project \
  --config-dir <same-config-dir> --select phase.chains/develop --reuse
```

Repeat `--select` for a subset, or use `--select '*'` for all displayed entries
only. The offer pins the repository identity, config directory, selected values
and their prior values. Saving uses an atomic repository update, preserves other
map entries, and refuses conflicting changes made since display. It does not
interpret chat replies or grant workflow/action authority. Plain confirmation,
a missing `--reuse`, or a failed save does not authorize saving anything else.

Direct formatter callers can use `--preference-config-dir` and
`--preference-offer-output` to retain the same displayed offer. Keep the offer
paired with the presented output; do not overwrite it before answering that
confirmation. Staged preparation is preferred because it pins these paths.

Optional request-level `preference_templates` accepts only `worktree.convention`,
`delivery.convention` and `cleanup.convention`, using the reusable shapes below.
The formatter checks their expansion against the current complete proposal;
literal current issue targets, mismatches or malformed templates are excluded
from saving with a visible diagnostic. A valid cached delivery template may
be supplied automatically when it still matches the proposal. Arbitrary commands
and custom paths are not reverse-engineered into templates. These values only
propose future defaults; they never execute actions or replace their authority.

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

### Reusable delivery template

Delivery evidence may include this optional structured template alongside its
existing `target`, `stable_conventions` and fingerprinted `sources`:

```json
{
  "delivery_template": {
    "deliver": [["gh", "pr", "merge", "--merge"]],
    "deliver_description": ["Merge the reviewed PR for {issue_name}."]
  }
}
```

The example is applicable only to a repository whose sourced delivery route
is a PR merge. First establish that route, then save its reusable template with
the existing `evidence refresh --category delivery` command. Do not save a prior
issue's concrete PR number or one-time target as a repository-wide convention.

Allowed substitutions are `{issue_name}`, `{issue_id}`, `{project_root}` and
`{worktree}`. Expansion operates on individual argv strings, with no shell or
attribute evaluation. Escape literal braces as `{{` and `}}`. Unknown or missing
substitutions, invalid argv shapes and mismatched descriptions remain gaps.
The command and description arrays must have equal lengths. Only a fully valid
cache hit supplies the template; stale, contradictory or corrupt records do not.
Explicit current commands (including `[]`) override the template. Old narrative
records remain readable but cannot be silently converted into executable routes.


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

## Supported proposal preferences

The normal `draft`/`assemble` consumer applies these reusable keys. Missing keys
fall through to existing policy/configuration defaults; malformed or graph-
incompatible values produce a missing decision with a diagnostic. Their lower-priority
fallbacks are not written into the draft as resolved choices: completing unrelated
gaps and rendering again retains the incompatibility until the saved value is
corrected/cleared or a deliberate current choice resolves it. Normal null action
placeholders consult saved conventions before policy defaults; explicit current
empty arrays still win. Current
values (including empty lists and `false`) take precedence. Repository values
precede user values; clearing a repository key exposes the user value.

| Key | Supported value |
| --- | --- |
| `conversation.locale` | Locale string. Saved explicit language precedes a current `inferred` locale; a current explicit locale wins. Existing workflow locale snapshots remain authoritative. |
| `manager.mode` | Existing formatter mode: `event-driven`, `attached`, or `unattended`. |
| `manager.event_manager` | Ordered CLI string array, applicable to `event-driven`. |
| `manager.poll_interval_seconds` | Positive integer, applicable to `attached`. |
| `worktree.convention` | Path template using the existing delivery-template placeholders, or `{"current_checkout":true}`. No worktree is created. |
| `phase.chains` | `{"steps":{"develop":["codex:MODEL"]},"roles":{"developer":["codex:MODEL","claude:FALLBACK"]}}`. Selectors must match the selected graph; a step entry precedes its role entry. Current phase entries precede both. Ordered fallback candidates remain subject to current suitability and availability decisions. |
| `confirmation.assignments` | `{"user_required":["STEP"],"manager_confirmable":["STEP"]}` using the existing assignable-gate partition. Mandatory task gates cannot be overridden here. |
| `review.decisions` | Step-to-decision mapping using existing formatter choices (`required`, `not_required`), subject to the selected graph's review eligibility. |
| `delivery.convention` | `{"deliver":[["git","-C","{worktree}","commit","-m","{issue_name}"]],"deliver_description":["Commit {issue_name}."]}`. |
| `cleanup.convention` | Same action-template shape with `cleanup` and `cleanup_description`; explicit empty arrays propose no cleanup. |

Action templates use the existing literal-argv renderer and its allowed
placeholders. They use the resolved checkout and issue context, execute nothing,
and confer no authority. These preferences supply proposal inputs; Manager still
judges strategy, suitability, exact targets, source freshness and action permission.
A saved value never confirms or activates a contract.

Generated delivery values carry `generated_inputs` provenance in the editable
request. Preserve that metadata when editing gaps. Unchanged generated values
must still have valid matching evidence and target context at render time. If
these change, reassess the action and edit the affected `formatter_inputs` field.
If reassessment deliberately retains the same value, use the advanced
`current_explicit_inputs` adapter and remove that field from `formatter_inputs`;
an unchanged value alone does not record reassessment. Never remove provenance
to bypass invalidation. Legacy requests without generated metadata retain
their explicit-input semantics. This metadata is freshness evidence, not authority.

Report capture publishes the raw report and request atomically per file. When
replacing a report already referenced by the draft, it uses a content-addressed
sibling so a failed request publication preserves the previous request and its
report, including relative or symlink reference spellings that resolve to the
same file. An interruption can leave an unreferenced report; retry safely retains
the decisions and publishes the new reference.

Malformed sibling model records remain inspectable misses with diagnostics; they
do not prevent a valid selected refresh. Source invalidation metadata is itself
validated, so damaged metadata cannot become a hit or disable shared-source
invalidation for other valid records.
