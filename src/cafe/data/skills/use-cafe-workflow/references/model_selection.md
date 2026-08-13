# Issue Assessment And Model Selection

Read this reference before kickoff, before writing phase execution config, and
after every completed phase. Also read `kickoff.md` before asking for
confirmation and `running_workflow.md` before execution.

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
- `fallback_strength`: `equivalent` or `equivalent_or_stronger`.

Do not infer this profile from a conventional step name. Resolve the skill bound
by the active playbook. For an iteration selector, kickoff conservatively
aggregates all variants and post-phase reassessment resolves the actual next
iteration. This applies equally to bundled and custom playbooks. A legacy custom
skill without a declaration receives the neutral default and the formatter marks
it `defaulted`; do not silently invent stronger or weaker requirements.

## Select exact chains

Combine the issue nature, scale, risk factors, each resolved execution profile, configured
providers, available exact models, and preflight evidence. The proposal must
name an ordered chain of at least two distinct CLIs with an exact model for each
entry. No provider or model is built into this skill.

Apply these rules:

1. Choose a primary that satisfies the phase reasoning and workload.
2. Choose a fallback that satisfies `fallback_strength`. For
   `equivalent_or_stronger`, do not knowingly select a weaker fallback.
3. Use a distinct fallback CLI so it can execute independently from the
   primary.
4. Raise the required capability when issue-level risk is stronger than the
   phase default. Never lower a declared high-risk phase merely because the
   overall issue is small.
5. Keep publication or other routine phases economical only when their own risk
   domains and current issue evidence permit it.

The driver may recommend any configured combination that meets these rules, but
must explain the choice using the issue assessment and execution profile.

## Model and fallback preflight

Before the first phase execution:

1. Render the exact ordered chain for every agent-executed phase: primary first,
   followed by every fallback.
2. Confirm each selected CLI is installed and authenticated. A version command
   proves installation only; use a minimal non-mutating prompt with every
   distinct selected CLI/model to prove that the exact model can execute.
3. Exercise the configured CAFE fallback path in a disposable test fixture by
   making the primary fail with the classified `model_not_found` condition and
   proving the configured fallback entry executes. Do not create a fake failure
   inside a live issue or consume one of its iterations.
4. Run `cafe crew list` and inspect the applicable `.cafe/phases.yaml` to verify
   ordering and exact model names. Treat an unavailable model, missing
   authentication, or failed fallback smoke test as a blocking preflight
   failure.

Automatic activation of a confirmed fallback is already authorized by kickoff;
it is not a driver-authored adjustment. Preserve the execution record showing
which CLI/model actually ran.

## Persist the confirmed plan

After kickoff confirmation and preparation, store issue-owned execution chains
in the active worktree's `.cafe/phases.yaml`; do not rewrite repository-wide
crew defaults for one issue:

```yaml
quality_gate:
  role: reviewer
  clis:
    - cli: <primary-cli>
      model: <exact-primary-model>
    - cli: <fallback-cli>
      model: <exact-fallback-model>
```

Persist the issue assessment and the explicit adjustment boundary in
`.cafe/issues/<issue-name>/issue.yaml`:

```yaml
issue_assessment:
  nature: feature/integration
  scale: medium
  risk_factors: [public contract, integration coverage]
model_adjustment:
  authority: driver_autonomous  # or user_approval_required
  confirmed_by: user
  confirmed_at: 2026-08-13
```

With `driver_autonomous`, the driver may change future phase chains using the
same selection and preflight rules. With `user_approval_required`, every
driver-authored change requires confirmation. Automatic use of a chain's
already configured fallback is not a driver-authored change.

## Reassess after every completed phase

After each one-step invocation, inspect the phase output, findings, actual
CLI/model, duration, verification evidence, and next baton. Reassess only the
unexecuted next phase or required correction; never rewrite historical
iteration metadata.

Keep the chain when scope and risk still match. Change it only with concrete
evidence, including:

- newly discovered security, migration, concurrency, or cross-subsystem risk;
- repeated incomplete corrections or a review exposing a missing contract;
- model/CLI unavailability, rate limiting, or materially poor output;
- remaining work becoming mechanical enough for a lower capability that still
  satisfies the resolved phase profile.

Update only the future phase's chain in `.cafe/phases.yaml`. With
`user_approval_required`, stop and obtain approval for the exact replacement
first. State the keep/change rationale in the driver progress update; do not add
a separate runtime decision store. A terminal `_done` baton has no future chain
to adjust.
