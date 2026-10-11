# Phase reasoning effort

Each `.cafe/phases.yaml` entry may carry `reasoning_effort` alongside `cli` and
`model`. It belongs to that entry, including each fallback. A worktree chain
replaces the repository chain as a whole; omitted effort never inherits another
entry's value. Omission preserves the provider's existing default. Explicit null,
blank, non-string and unsupported tokens are configuration errors.

`workflow.execution_profile.reasoning` describes the provider-neutral workload
minimum (`routine`, `standard`, `high`). It is not a provider effort token or a
model ranking. The Manager selects the capability band, exact model and supported
effort together and explains both choices for the phase's workload and risk.
Equal token names across providers do not prove equivalent capabilities.

## Verified admission

The following bounded projections were checked on 2026-10-11. Explicit admission
verifies the installed client version and exact model; unknown combinations fail
before launch. These are adapter capabilities, not phase-skill model defaults.

- Codex 0.159.3 and later uses invocation `--config model_reasoning_effort="LEVEL"`
  on fresh and resumed executions. Native app-server execution applies the
  admitted value through thread config and every turn's `effort` RPC field;
  response `reasoningEffort` confirms thread settings, not per-turn internal
  thinking telemetry. Independently modeled forks must admit the inherited token
  too; unknown child combinations reject before launch. The selected model's levels come from the
  installed client's version-matching `models_cache.json`; no family-name or
  token-count inference is used. CLI configuration overrides inherited defaults.
  [Codex configuration reference](https://developers.openai.com/codex/config-reference/).
- Claude Code 2.1.289 and later uses `--effort` plus the same value in the child
  `CLAUDE_CODE_EFFORT_LEVEL`, preserving the host environment. Exact documented
  model ranges differ: Opus/Sonnet 4.6 exclude `xhigh`. Local and managed
  `maxEffortLevel` caps and known thinking-disable controls are checked. A cap
  cannot be removed by a project default. Provider reports are reconciled and
  conflicting reports reject success. Remote enterprise restrictions unavailable
  to local inspection are not represented as provider-confirmed evidence.
  [Claude model configuration](https://code.claude.com/docs/en/model-config).
- Copilot CLI 1.0.83 and later uses `--effort` on fresh and resumed commands for
  the verified conservative subset in its adapter. Unknown models and alternate
  provider contexts reject explicit effort. The subset is intentionally narrower
  than all models offered by the provider.
  [Copilot CLI reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference).
- Gemini and Cursor retain legacy omission but reject explicit effort. Gemini's
  documented model overrides include named thinking levels, but no independent
  invocation-scoped override preserving managed settings was verified for this
  delivery. CAFE does not replace its system settings to force a level or
  approximate it with a token budget. Cursor's inspected parameter reference
  supplies no verified independent effort mechanism. No temporary settings are
  created, so no provider-settings cleanup is needed on their rejection paths.
  [Gemini configuration](https://geminicli.com/docs/reference/configuration/),
  [Cursor parameters](https://cursor.com/docs/cli/reference/parameters).

## Execution and audit

Admitted invocation snapshots freeze complete entries. Retry repeats the same
entry; fallback uses its own effort. Sticky state supplies only a configured CLI
preference. A changed effort invalidates its tuple fingerprint. Exact resume
keeps the requested session identity and reapplies the current entry. A supplied
compact execution projection must agree with current phase YAML, or setup rejects
it before launching a provider.

`reasoning_evidence` distinguishes `configured`, `applied`, `reported`,
`verification` and `provenance`. Physical `execution_attempts` include failures
and success. A native report establishes `provider-confirmed`; a validated
command establishes `invocation-derived`; omission without a report remains
`unknown/CLI-default`. Neither a successful exit nor reasoning-token usage proves
a level or the model's internal thinking. Provider mismatch invalidates success.
Existing iteration records remain readable without rewriting their history.

Candidate cache receipts separate explicit effort and omission and include
executable/version, adapter/runtime projection and opaque effective-control
fingerprints, including the actual execution workspace. Invocation admission
reads project controls in that workspace, without changing the host directory.
Actual probes run on misses. Failed, interrupted and mismatched
probes cannot cache success. Ordered fallback smoke remains provider-free and
checks the production Manager's per-entry takeover contract. Primary-only chains
never gain a fallback.

Future adjustments retain existing human confirmation, predecessor CAS and
freshness checks. Update YAML and the confirmed contract consistently, then
regenerate launch/resume context. These writes are not a cross-file transaction;
partial updates fail consistency checks. Already admitted and completed phases
are immutable. Reassessment alone grants no mutation authority.
