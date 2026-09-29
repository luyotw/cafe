# Issue 573 Kickoff Preparation Measurement

## Status

Measurement is pending. No Manager preparation observations have been recorded. This report will not substitute helper timing, cache status, or simulated reasoning for Manager-level evidence.

The required pre-change revision is `6ce6bddade03e6ee31a60f437accd4d557467c50`. The current development worktree has not changed normal Manager preparation guidance, so that baseline remains reproducible.

## Fixed cases

The accepted plan defines two representative cases. Their exact request text, accepted answers/preferences, repository snapshot, effective catalog state, delivery endpoint, and Manager invocation settings must be fixed before the first run and reused unchanged across conditions.

| Case | Required shape | State |
| --- | --- | --- |
| A | A bounded change with a straightforward effective graph. | Exact request and accepted inputs pending. |
| B | A request requiring multiple phase requirements and repository-informed delivery. | Exact request, delivery endpoint and accepted inputs pending. |

The planned conditions are baseline at the pre-change revision, improved cold with isolated empty stores (including preference save cost), and improved warm in a fresh Manager context. Each condition requires three actual runs per case. A separate changed-input case requires three actual runs.

## Environment and invocation

- Repository: `/home/luyotw/cafe`
- Baseline revision: `6ce6bddade03e6ee31a60f437accd4d557467c50`
- Implementation revision: to be recorded after the code is complete.
- Python environment: CPython 3.12.2, as reported by the repository pre-commit hook.
- Available Manager CLI executables: `claude`, `gemini`, and `codex` are installed in the local environment.
- Exact Manager CLI, model/version and reasoning settings: pending selection.
- No Manager CLI run has been started for this measurement.

## Run records

No run records are available yet. For every run, record the exact request and accepted inputs, source revision, repository/catalog/evidence state, CLI/model/reasoning settings, start and end timestamps, complete rendered proposal reference, tool/request-result boundaries, evidence bytes delivered, external research spans, Manager reasoning/orchestration residual, and human waiting separately.

Report end-to-end elapsed time and preparation excluding human waiting. Use the wall-time union for concurrent tool spans. Count only unresolved-choice questions as clarifications; report mandatory final confirmation separately. Report each observation and median/range, cold overhead, changed-input cost, and regressions.

| Case | Condition | Run | Proposal evidence | Elapsed | Tool spans | Evidence bytes | Clarifications | Human wait | State |
| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | --- |
| A | Baseline | 1–3 | Pending | — | — | — | — | — | Not run |
| A | Cold | 1–3 | Pending | — | — | — | — | — | Not run |
| A | Warm | 1–3 | Pending | — | — | — | — | — | Not run |
| B | Baseline | 1–3 | Pending | — | — | — | — | — | Not run |
| B | Cold | 1–3 | Pending | — | — | — | — | — | Not run |
| B | Warm | 1–3 | Pending | — | — | — | — | — | Not run |
| Changed input | Improved warm with one material input changed | 1–3 | Pending | — | — | — | — | — | Not run |

## Unresolved inputs

The plan describes the case shapes and metrics but does not supply the exact request text, accepted answers/preferences, delivery endpoint, or exact Manager CLI/model/reasoning configuration. The three installed provider CLIs can incur external service usage; no run has been started while these run inputs and authorization remain unresolved.

After those inputs are settled, populate the run records and compare complete semantically equivalent proposals. If actual Manager telemetry or access is unavailable, preserve the limitation and leave the measurement acceptance unmet.
