# Issue Assessment And Model Selection

Read this reference before kickoff, before writing phase execution config, and
whenever execution returns control with agent phases still unexecuted. Also read
`kickoff.md` before asking for confirmation and `running_workflow.md` before
execution.

## Assess before proposing models

Read the issue, relevant strategic documents, nearby implementation, existing
tests, dependencies, and linked issues. Record:

- `issue_nature`: the dominant kind of work, such as documentation/config,
  localized defect, feature/integration, refactor, migration, or security/trust
  boundary;
- `issue_scale`: `small`, `medium`, or `large`;
- `risk_factors`: cross-subsystem behavior, durable schema/state, migration or
  compatibility, concurrency, security boundaries, external side effects, and
  unusually broad verification;
- `rationale`: concrete repository evidence, not issue-label inference alone.

Use these scale defaults as guidance, not line-count quotas:

| Scale | Default evidence |
| --- | --- |
| `small` | One subsystem, localized behavior, no durable-contract change, and a focused verification surface. |
| `medium` | Several connected components or one public contract, with bounded integration coverage. |
| `large` | Cross-cutting runtime behavior, more than two subsystems, durable migration, security/trust boundaries, or a broad integration matrix. |

If the proposed scope is `large`, perform the issue-decomposition assessment
before confirmation. Do not compensate for an issue that should be split merely
by assigning a stronger model.

## Resolve phase execution requirements

Every phase skill should declare a provider-neutral
`workflow.execution_profile`:

- `workload`: the kind of work performed;
- `reasoning`: `routine`, `standard`, or `high`;
- `risk_domains`: stable failure surfaces;
- `fallback_strength`: `equivalent` or `equivalent_or_stronger`; this constrains
  a fallback when one is configured and does not require a fallback to exist.

Do not infer this profile from a conventional step name. Resolve the skill bound
by the active playbook. For an iteration selector, kickoff conservatively
aggregates all variants so every execution mode has a valid initial chain.
Continuous mode does not pause at phase boundaries. Single-step mode may resolve
the actual remaining iteration when control returns to the driver. This applies
equally to bundled and custom playbooks. A legacy custom skill without a
declaration receives the neutral default and the formatter marks it `defaulted`;
do not silently invent stronger or weaker requirements.

## Keep model ownership outside phase agents

The phase skill owns only its provider-neutral minimum execution profile. The
driver owns the capability-band classification, current provider/model mapping,
preflight, and every write to the active worktree's `.cafe/phases.yaml`.

- Do not put provider names, model IDs, or cost tiers into phase skills.
- Do not let a phase agent select its own model or approve its fallback.
- Treat an existing repository chain as a candidate, not as selection evidence.
  Re-justify it against the current issue and phase before proposing it.
- Keep exact model choices issue-owned. A reusable phase skill must remain valid
  when providers rename, replace, or reposition models.

## Classify the required capability band

Classify the remaining work for each phase before mapping it to current models.
The bands describe task requirements, not permanent provider or model names.

| Band | Use when | Do not use when |
| --- | --- | --- |
| `efficiency` | The work is routine and bounded, output shape is explicit, automated or host-side validation is strong, failure is reversible, and the agent owns no ambiguous or high-consequence decision. | Correctness depends on discovering unstated scope, coordinating several subsystems, or detecting a subtle failure that validation will not expose. |
| `balanced` | Scope and contracts are clear, the work still needs ordinary engineering judgment across connected components, tests or review provide a bounded correction loop, and failure is recoverable. | The task changes a durable contract or migration boundary, has substantial unknowns, or a locally plausible result can leave a system-wide defect. |
| `frontier` | Requirements or architecture contain material unknowns; work crosses subsystem, migration, compatibility, durable-state, concurrency, security, or trusted-capability boundaries; verification is broad; or failure is hard to detect or undo. | The remaining work has become mechanical and independently verifiable. |

Use the phase profile as the minimum starting point:

- `reasoning: routine` is eligible for `efficiency` only when the task evidence
  also satisfies the boundedness, verification, and reversibility conditions.
- `reasoning: standard` normally starts at `balanced`.
- `reasoning: high` normally starts at `frontier`. Select a `balanced` model only
  with concrete current evidence that it satisfies the declared high-reasoning
  requirement for this workload; cost or an existing preset is not evidence.

Then apply issue-level escalation. A medium public contract or several connected
components requires at least `balanced`. Large scope, broad unknowns, a breaking
migration, multiple sources of truth, security/trust boundaries, durable state,
concurrency, weak observability, or difficult rollback raises affected phases to
`frontier`. Issue risk may raise a phase above its default; it never lowers the
phase skill's declared minimum.

Classify the work the agent actually owns. A publication phase may remain
`efficiency` when the agent only prepares a deterministic local artifact and an
independent trusted host hook validates and performs the external mutation. If
the agent itself must resolve ambiguous review feedback, redesign behavior, or
authorize the side effect, route that work to the responsible earlier phase or
raise the band.

## Select exact chains

Combine the issue nature, scale, risk factors, each resolved execution profile, configured
providers, available exact models, and preflight evidence. The proposal must
name an ordered chain with one primary CLI/exact model and zero or more
fallback CLI/exact model entries. A user may choose a primary-only chain; do not
add a fallback after that explicit choice. No provider or model is built into
this skill.

Map the selected band to current provider models using current primary provider
documentation and task-relevant local evidence. Do not permanently equate a
band with a provider family, infer capability from a model name alone, or use a
repository preset as the rationale. If current positioning is unavailable or
cross-provider equivalence is uncertain, retain a previously confirmed capable
chain or choose the stronger candidate rather than guessing.

Apply these rules:

1. Choose a primary that satisfies the phase capability band, reasoning, and workload.
2. If a fallback is configured, choose one that satisfies `fallback_strength`. For
   `equivalent`, use the same assessed band. For `equivalent_or_stronger`, use
   the same or a stronger assessed band; if equivalence is uncertain, choose
   the stronger fallback.
   Model release order is not capability-band order: a fallback may use an
   older model version than the primary when current provider evidence,
   task-relevant local evidence, and preflight show that it still satisfies the
   required band, workload, reasoning, and risk domains. Never reject a
   fallback solely because its version number is lower, and never assume two
   versions are equivalent solely because they share a model family.
3. Every configured fallback uses a distinct CLI so it can execute
   independently from the primary and other entries.
4. Raise the required capability when issue-level risk is stronger than the
   phase default. Never lower a declared high-risk phase merely because the
   overall issue is small.
5. Keep publication or other routine phases economical only when their own risk
   domains and current issue evidence permit it.
6. Prefer a canonical exact model identifier reported by the provider or CLI.
   Do not persist a floating alias as "exact" when the preflight exposes the
   canonical model it resolved to.

The driver may recommend any configured combination that meets these rules, but
must record one phase-specific rationale naming the selected band, profile
evidence, issue-risk overlay, and, when configured, why each fallback meets its
strength contract. For a primary-only chain, record that explicit choice and
its hard-stop consequence instead.

## Model and fallback preflight

Before the first phase execution:

1. Render the exact ordered chain for every agent-executed phase: primary first,
   followed by every fallback.
2. For every distinct selected CLI/model, consult the machine-local preflight
   cache described below. On a cache miss, confirm the CLI is installed and
   authenticated with an actual minimal non-mutating model prompt. A version
   command proves installation only; it is used for cache invalidation, not as
   model-availability evidence.
3. For every distinct ordered chain that has at least one fallback, run the
   cached CAFE fallback smoke helper.
   A fresh smoke exercises the configured path by making the primary fail with
   the classified `model_not_found` condition and proves each configured
   fallback entry can execute in order. Do not create a fake failure inside a
   live issue or consume one of its iterations; use the disposable in-process
   fixture provided by the helper. A primary-only chain skips fallback smoke
   because there is no takeover path to test.
4. Inspect the applicable `.cafe/phases.yaml` and resolved execution preview to
   verify ordering and exact model names. Treat an unavailable model, missing
   authentication, or failed fallback smoke test as a blocking preflight
   failure.

Automatic activation of a confirmed fallback, when configured, is already
authorized by kickoff; it is not a driver-authored adjustment. With a
primary-only chain, a primary failure is a hard stop until the existing model-
adjustment authority permits a replacement or the user confirms one. Preserve
the execution record showing which CLI/model actually ran.

### Reuse successful preflight evidence

Use `scripts/preflight_cache.py` to run or reuse the model probe. The cache is
machine- and user-local at the XDG cache location (normally
`~/.cache/cafe/use-cafe-workflow/preflight-v1.json`); never put it in the
repository or an issue worktree. It stores timestamps, exact model names, CLI
version fingerprints, ordered chains, and CAFE runtime fingerprints. It does
not store prompts, model output, credentials, tokens, or repository content.

For each distinct selected candidate, run:

```bash
python3 <skill-dir>/scripts/preflight_cache.py candidate-probe \
  --cli <cli> --model <exact-model>
```

- Exit `0` with `status=hit` reuses a successful actual execution from the last
  24 hours for the same exact model and unchanged CLI executable/version.
- Exit `0` with `status=fresh` means the helper ran one actual minimal
  non-mutating prompt through the selected CAFE CLI/model and cached the success.
  If the provider reports a canonical resolved model, use that exact identifier
  in the confirmed chain and probe it before execution.
- Any non-zero exit is a blocking probe, cache, or CLI inspection error. The
  helper never records a failed, interrupted, rate-limited, unauthenticated, or
  ambiguous probe.

For chains with fallbacks, smoke each distinct ordered chain; the helper
automatically reuses a successful result for 30 days only when the exact chain
and CAFE runtime source fingerprint are unchanged:

```bash
python3 <skill-dir>/scripts/preflight_cache.py fallback-smoke \
  --entry <primary-cli>:<exact-model> \
  --entry <fallback-cli>:<exact-model>
```

Run this command only when a fallback exists. Pass every configured fallback
with another `--entry` in execution order. A
`status=fresh` result performed the disposable smoke; `status=hit` reused it.
The helper never calls a provider model: actual provider execution is covered by
the candidate probes, while this smoke proves CAFE's classified takeover path.

A live workflow failure always overrides cached evidence. On
`model_not_found`, authentication failure, or CLI unavailability, invalidate the
matching candidate before reconsidering it:

```bash
python3 <skill-dir>/scripts/preflight_cache.py candidate-invalidate \
  --cli <cli> --model <exact-model>
```

For a suspected fallback-runtime defect, rerun `fallback-smoke` with `--force`.
Do not cache failures or treat a cache hit as permission to ignore current rate
limits, provider incidents, or a different resolved model.

## Persist the confirmed plan

After kickoff confirmation and preparation, store issue-owned execution chains
in the active worktree's `.cafe/phases.yaml` with
`scripts/write_phase_config.py`. Pass only the exact confirmed dynamic chains,
then resolve every required step through the core parser before execution:

```yaml
quality_gate:
  name: Reviewer
  role: reviewer
  clis:
    - cli: <primary-cli>
      model: <exact-primary-model>
```

Append further `clis` entries only for confirmed fallbacks. A single entry is
a valid primary-only chain.

Persist the issue assessment and the explicit adjustment boundary in
`.cafe/issues/<issue-name>/issue.yaml`:

```yaml
issue_assessment:
  nature: feature/integration
  scale: medium
  risk_factors: [public contract, integration coverage]
model_adjustment:
  authority: driver_autonomous  # or user_approval_required
```

With `driver_autonomous`, the driver may change future phase chains using the
same selection and preflight rules. With `user_approval_required`, every
driver-authored change requires confirmation. Automatic use of a chain's
already configured fallback, when present, is not a driver-authored change.

## Reassess at contract-defined boundaries

In `continuous` mode, do not stop execution merely to reconsider a successful
phase's model choice. Reassess when CAFE naturally pauses for a user, an error,
or a required correction. In `single_step` mode, reassess after every completed
step before explicitly continuing. Inspect the completed phase output,
findings, actual CLI/model, duration, verification evidence, and next baton;
change only the still-unexecuted phase or required correction and never rewrite
historical iteration metadata. If CAFE reaches `done`, record the actual model
evidence but do not perform a model-selection pause with no future phase to
configure.

Keep the chain when scope and risk still match. Change it only with concrete
evidence, including:

- newly discovered security, migration, concurrency, or cross-subsystem risk;
- repeated incomplete corrections or a review exposing a missing contract;
- model/CLI unavailability, rate limiting, or materially poor output;
- remaining work becoming mechanical enough for a lower capability that still
  satisfies the resolved phase profile.

De-escalate only future work. Reduced uncertainty after spec or plan may move a
future `standard` implementation from `frontier` to `balanced` when its
contracts, deletion/wiring map, tests, and rollback are now explicit. It does
not justify lowering a `high` phase or an unresolved migration merely because a
stronger phase produced a good artifact. An implementation correction may move
to `efficiency` only when it is deterministic, narrowly verified, reversible,
and still satisfies the phase profile.

Update only the future phase's chain in `.cafe/phases.yaml`. With
`user_approval_required`, stop and obtain approval for the exact replacement
first. State the new band and keep/change rationale in the driver progress
update; do not add a separate runtime decision store. A terminal `_done` baton
has no future chain to adjust.

For a Driver-managed issue, `.cafe/phases.yaml` remains generic execution
configuration under its existing lifecycle. The Driver contract keeps only its
own confirmed phase/model authority and never compares or projects that generic
file. A delegated replacement may change only explicitly delegated Driver model
paths; proactive-review policy, confirmation ownership, or any unrelated
Driver policy requires user reconfirmation and a complete contract replacement.
