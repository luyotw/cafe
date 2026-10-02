# Language Policy

This is the single source of truth for the question "which language should this
piece of text be in?". Manager, playbook, agent, and phase documentation defer to
this document; none of them states a competing precedence or a competing
authority.

The normal policy resolves exactly **two** language settings:

- **Repository content language** — the language this project writes its own
  documentation, code comments, and engineering artifacts in. It is confirmed
  once, repository-wide, in `.cafe/strategic_context.yaml` under
  `repository_language.content_locale`.
- **Workflow conversation language** — the language the system talks to *this
  user* in, for *this* workflow run. The workflow's own state owns it.

A task-specific delivery language belongs in that individual task or artifact
contract. There are no per-channel, per-agent, or per-recipient language
settings.

## Classification by purpose

Classify content by what it is *for*, not by where the file lives and not by
whether a person reads it.

| Content | Language |
| --- | --- |
| Repository documentation, code comments, maintainer policy and skill documentation | Repository content language; explicit scoped exceptions are preserved |
| Engineering spec, plan, review, and PR prose | Repository content language by default; commits follow existing repository conventions |
| HumanTask questions, choices, confirmation text, user-facing explanations | Workflow conversation language |
| Manager conversation, workflow progress, and Slack notifications | Workflow conversation language |
| Customer-facing, editorial, research, or translation deliverables | The explicit target-audience or task delivery language stated in the task or artifact contract |
| Workflow-scoped CLI presentation | The workflow conversation language in the surfaces listed under [Support boundary](#support-boundary) |
| IDs, schema keys, enum values, commands, paths, error codes, parser markers | Stable and untranslated |

An agent's own declared language never overrides an explicit artifact language,
a repository convention, or the human-interaction language. This policy makes no
claim about, and does not try to verify, a model's internal reasoning language.

## Resolving the workflow conversation language

Precedence, highest first:

1. a current explicit instruction for this workflow;
2. an applicable explicit repository-scoped saved preference;
3. an explicit user-scoped saved preference;
4. a preference reliably inferred from the user's own natural-language messages;
5. the active playbook's explicit `playbook.conversation_locale`;
6. `en-US`.

The staged Manager helper resolves saved preferences for a new proposal in
repository-before-user order and reports their scope and provenance. A current
explicit instruction overrides either saved scope. A saved preference is
reused only after an explicit request to save it; a one-off answer is not
persisted. Inference retains `inferred` provenance and does not become an
explicit saved preference. These values supply the existing workflow-language
input; they do not add another language value or change the runtime's ownership
of an already-created workflow.

Inference ignores quoted material, code, stack traces, logs, generated
artifacts, and isolated tokens such as `1` or `ok`. Mixed or ambiguous evidence
is not a preference — a caller with no reliable evidence supplies nothing and
resolution falls through to the next tier.

`auto` is only the unresolved marker. It is never stored as an effective
language. An unrecognized or malformed tag is not usable evidence either: it
falls through a tier rather than corrupting stored state.

Simplified and Traditional Chinese are never treated as interchangeable.

## Ownership

The generic runtime owns validation, storage, inheritance, and propagation
(`src/cafe/core/conversation_locale.py`, `src/cafe/core/blackboard.py`). Generic
code never imports Manager code and never reads a Manager contract as its locale
authority.

A caller — the direct CLI or the Manager — may *supply* a preference through one
generic input contract, and must declare which tier it is supplying, so an
inferred preference is persisted as inferred and never relabelled as explicit.
Callers do the inferring; the contract owns the tiers and the exclusion rules
above.

The Manager reaches this contract through a Manager-owned adapter. Its
`locales.conversation` snapshot **mirrors** the workflow's effective value and
source rather than competing with it; on resume the adapter reads the effective
generic value instead of re-resolving. A confirmed Manager contract, including a
legacy Driver contract selected for continuation, is never silently rewritten on
resume — contract compare-and-set and reconfirmation stay user-owned.

## Initialization, change, and resume

**Automatic initialization is creation-only.** The locale is written when the
workflow state is first created, at either state-creation boundary:

- `cafe prepare` (`src/cafe/ui/commands/lifecycle.py`), which creates workflow
  identity; and
- `cafe workflow` (`src/cafe/ui/commands/workflow.py`).

Both accept `--conversation-locale` together with `--conversation-locale-source`
(`explicit` or `inferred`). A locale supplied without a declared tier is
rejected, and the rejection leaves no partial state behind.

After creation the locale is never written automatically again. A record that
already carries one keeps it. A record that carries none — every workflow
created before this contract — **stays without one** across load, save, repeated
preparation, and resume, even when a caller supplies a preference or the
playbook default changes. Absence is never backfilled and is never reported as a
user preference; presentation for such a record resolves the English fallback at
render time and writes nothing back.

**A deliberate workflow-language change is a separate, explicit operation** on
the same owning state: `cafe workflow --set-conversation-locale <tag>
--conversation-locale-source explicit`. It updates the stored value and source
so that *subsequently* materialized tasks and *subsequently* sent notifications
use the new language. It never translates or rewrites an already-pending task.

None of the following changes the stored language: an ordinary resume, a resume
that supplies a preference, a changed playbook default, a different host machine
or chat session, a different agent picking up the next step, or a request to
answer *this one reply* in another language. Only the explicit operation can.

## Pending work is preserved

A task's prompt, options, and canonical expected answer are captured as a
snapshot when the task is materialized. Every renderer and answer path —
interactive collection, CLI inspection, and validation of the submitted answer —
consumes that persisted snapshot and performs no locale re-resolution. An
outstanding confirmation therefore stays answerable across a language change:
its options and expected answer remain valid.

Localization never changes machine identity. Decision `id`s, schema keys,
patterns, input schemas, question options, allowed targets, commands, paths,
error codes, task ownership, permissions, and continuations are the same in
every language.

## Support boundary

Accepting a language tag is not the same as supporting it. The honest current
boundary is:

**Follows the workflow conversation language**

- HumanTask Slack notifications and callback-failure Slack notifications, from
  developer-authored text in **English and Traditional Chinese only**.
- Declared HumanTask prompts, decision labels, and confirmation text that carry
  an authored variant for that language, resolved when the task is materialized.
- Dynamic agent output — clarification questions, task prompts, and replies
  addressed to the user — because the phase prompt states the workflow language
  to the agent.

**Does not follow it today**

- General CLI help, command output, and diagnostics are English. This records
  current behavior. It is not a statement that the English baseline is
  permanent, and it is not a commitment to localize it later.
- Any locale with no authored text falls back to English **for that message
  only**. The fallback is silent: nothing is added to the delivered message, no
  separate warning is sent, and the workflow's stored locale is unchanged. A
  later message in a supported language is unaffected.
- A declared HumanTask item with no authored variant for the workflow language
  keeps its declared text.

A channel that cannot render a language's script is unsupported for that
language rather than silently approximated.

## Developer-authored runtime copy

Place localized copy with its owner, separate from selection and runtime behavior:

- Core runtime and Manager presentation use `src/cafe/data/locales/en-US.yaml`
  and `src/cafe/data/locales/zh-TW.yaml`.
- Phase-owned HumanTask copy uses
  `src/cafe/data/skills/<skill>/locales/en-US.yaml` and
  `src/cafe/data/skills/<skill>/locales/zh-TW.yaml`, alongside its `SKILL.md`.
  Project and global skill owners use the same structure in their selected skill
  directory.

Each owner has its own catalog pair. Add new phase copy to that phase's resources;
keep it out of the central runtime catalogs. This organization does not imply
general CLI translation or additional languages.

Each catalog is a flat YAML mapping from stable, untranslated message keys to
non-empty strings. Use lowercase names separated by dots, with underscores or
digits within a name, for example `workspace.correction`. Add every new key to
both of the owner's catalogs, with the same named placeholders in each language. Duplicate
keys, invalid YAML, missing keys, incompatible placeholders, and non-string
messages raise `LocaleCatalogError` with resource and message context rather
than silently accepting incomplete authored data.

Use simple `{name}` placeholders with ASCII identifiers. Attribute/index access,
format specifications, conversions, and positional fields are unsupported. Write
`{{` and `}}` for literal braces in a template. YAML literal blocks (`|-`) are
useful for multi-line copy without an extra trailing newline. For example, add
the same key and placeholder contract to each catalog:

```yaml
workspace.example: |-
  Affected path: {path}
```

Call `cafe.core.runtime_locales.render_text("workspace.example", locale=locale,
path=bounded_path)` from generic runtime code. Supply exactly the declared
placeholder names using string or integer values. Bound untrusted values at the
consumer's existing diagnostic boundary before rendering; the workspace
correction consumer retains `bounded_workspace_reason` and its UTF-8 limits.
Interpolation happens once, so braces inside inserted paths or diagnostics stay
literal. Keep ownership, permissions, budgets, and execution behavior in Python.

`load_catalogs()` reads the central resources through `importlib.resources`.
For owner resources, pass the resolved locale directory to
`load_catalogs(catalog_root)` or
`render_text(key, locale=locale, catalog_root=catalog_root, **values)`.
The generic loader validates each pair separately and returns immutable mappings.
Only the immutable packaged runtime pair is cached. Selected skill resources are
read afresh under the catalog read lock; a same-path publication or missing
current file cannot be hidden by a prior owner read. Each owner read loads its
two fixed locale files, without retaining owner cache entries. A supplied root is authoritative: it does not search other
owners or fall back to the central catalogs. Keys do not select a resource owner.
Packaged resources work independently of the current working directory in a
source checkout or installed distribution.
`render_text` delegates selection to the existing `select_text_locale` resolver.
Traditional Chinese aliases and silent English fallback remain unchanged;
rendering never updates the workflow's stored locale or imports Manager policy.

### HumanTask declarations and snapshots

A presentation field can reference a catalog message with
`{message_key: human_task.cafe_spec.output_review.prompt}`. Use the same reference
in the base `prompt` and its explicitly authored `prompt_locales` entries:

```yaml
prompt: {message_key: human_task.cafe_spec.output_review.prompt}
prompt_locales:
  zh-TW: {message_key: human_task.cafe_spec.output_review.prompt}
```

References are supported in policy/question prompts, decision labels, correction
guidance, and their existing locale-variant maps. They must contain only a string
`message_key` naming a message without interpolation arguments in the owning
skill's catalog pair. `SkillLoader` and workflow composition bind the directory
from the resolved skill catalog entry, including primary and shared contributors.
They pass the transient `locale_catalog_root` validation context before any
nested presentation fields are validated. The step name and message key never
guess this path. A reference without an owner is invalid. References expand at
declaration validation to the same plain strings used by inline declarations.
Custom inline copy and locale maps continue to work unchanged. `for_locale`
retains exact canonical-tag variant matching and declared-text fallback: a
`zh-TW` variant alone does not create a `zh-Hant` or `zh-HK` declaration variant.
Manager presentation retains its existing `zh-TW`/`zh-Hant` selection before
rendering; notification and correction selection use the canonical text resolver.

Materialized HumanTasks persist plain prompts, decisions, and correction guidance
with the existing snapshot schema. Reading or answering an outstanding task never
resolves a catalog reference again. IDs, input schemas, options as answer identity,
flags, routes, permissions, and ownership remain in their declarations. Do not
replace these with translated keys or retranslate agent-generated questions.

### Locale owner, key, and consumer inventory

| Copy owner / catalog directory | Catalog keys | Runtime consumer |
| --- | --- | --- |
| Runtime: `data/locales/` | `workspace.correction` | Bounded workspace correction prompt |
| Runtime: `data/locales/` | `notification.*` | HumanTask and failed-callback Slack payloads, including labels, fallback text, separators and closings |
| Manager: `data/locales/` | `manager.progress.*` | Missing state, iteration/status/review/confirmation/closeout presentation and localized punctuation |
| Manager: `data/locales/` | `manager.kickoff.*` | Localized table headers, checks, confirmation, empty closeout, command authorization and optional catalog-sync reminder |
| Owning phase: `data/skills/<skill>/locales/` | `human_task.<skill>.<task>.*` | HumanTask policy validation, then existing task materialization and snapshots |

The phase catalog inventory covers `cafe-brief_first`, `cafe-brief_revise`,
`cafe-develop`, `cafe-draft`, `cafe-incident_detect`, `cafe-incident_mitigate`,
`cafe-incident_postmortem`, `cafe-incident_triage`, `cafe-plan`, `cafe-pr`,
`cafe-qa`, `cafe-research_collect`, `cafe-research_question`,
`cafe-research_report`, `cafe-research_synthesize`, `cafe-review`, and `cafe-spec`.
These 17 owners contain 43 keys for 26 policies; the runtime pair contains 60
keys (1 workspace, 32 notification, 19 progress, 8 kickoff), with no duplicated
phase copy. The phase inventory includes every declared localized prompt and decision label, output/no-change
confirmations, and both editorial question prompts. No packaged question options
or correction-guidance variants had localized copy to move. Their identity and
custom authored variants remain supported. Monolingual CLI/help and unlocalized
runtime defaults, repository instructions/documentation/examples, detector
vocabulary, locale settings, and presentation symbols are outside this inventory.
