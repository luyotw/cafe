# Language Policy

This is the single source of truth for the question "which language should this
piece of text be in?". Driver, playbook, agent, and phase documentation defer to
this document; none of them states a competing precedence or a competing
authority.

There are exactly **two** normal policy inputs:

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
| Driver conversation, workflow progress, and Slack notifications | Workflow conversation language |
| Customer-facing, editorial, research, or translation deliverables | The explicit target-audience or task delivery language stated in the task or artifact contract |
| Workflow-scoped CLI presentation | The workflow conversation language in the surfaces listed under [Support boundary](#support-boundary) |
| IDs, schema keys, enum values, commands, paths, error codes, parser markers | Stable and untranslated |

An agent's own declared language never overrides an explicit artifact language,
a repository convention, or the human-interaction language. This policy makes no
claim about, and does not try to verify, a model's internal reasoning language.

## Resolving the workflow conversation language

Precedence, highest first:

1. an explicit user instruction;
2. a preference reliably inferred from the user's own natural-language messages;
3. the active playbook's explicit `playbook.conversation_locale`;
4. `en-US`.

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
code never imports Driver code and never reads a Driver contract as its locale
authority.

A caller — the direct CLI or the Driver — may *supply* a preference through one
generic input contract, and must declare which tier it is supplying, so an
inferred preference is persisted as inferred and never relabelled as explicit.
Callers do the inferring; the contract owns the tiers and the exclusion rules
above.

The Driver reaches this contract through a Driver-owned adapter. Its
`locales.conversation` snapshot **mirrors** the workflow's effective value and
source rather than competing with it; on resume the adapter reads the effective
generic value instead of re-resolving. A confirmed Driver contract is never
silently rewritten on resume — contract compare-and-set and reconfirmation stay
user-owned.

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
