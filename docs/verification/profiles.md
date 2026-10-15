# Repository verification profiles

Adoption is optional. A repository can bind `.cafe/config.yaml`:

```yaml
verification:
  profile_file: verification/profiles.json
  profile: acceptance
```

Only the project configuration is read for this binding; issue overrides do not
replace it. Explicit `--profile-file` and `--profile` take precedence together.
Malformed explicit adoption is an error. Without adoption, existing raw-command
`run/check/reuse/focus` behavior remains available with its existing limitations.

The JSON manifest has `schema_version: 1`, a `profiles` object, and a `suites`
object. Each profile defines nonempty `mandatory` IDs and optional `optional` IDs.
Each suite defines repository-root `argv` (no implicit shell), optional boolean
`reuse` (default false), and optional `context` with `files`, `env`, and `probes`.
File inputs are repository-contained content dependencies; environment inputs are
explicit names whose effective values are captured. Probe entries are argv lists
that demonstrate actual capabilities through successful exit and stable output.
Declare only non-secret values needed for reuse. No arbitrary environment dump is
recorded. Reuse declarations must cover the inputs actually needed by the suite;
CAFE cannot infer hidden behavioral coverage or undeclared dependencies.

```json
{"schema_version":1,"profiles":{"acceptance":{"mandatory":["bootstrap","logout"],"optional":[]}},"suites":{"bootstrap":{"argv":["python3","checks.py","bootstrap"],"reuse":true,"context":{"files":["checks.py"],"env":[],"probes":[]}},"logout":{"argv":["python3","checks.py","logout"],"reuse":false}}}
```

Both configured QA and CI call the same public entry:

```sh
cafe verification run --output-file .cafe/evidence/qa/output.md
cafe verification run --profile-file verification/profiles.json --profile acceptance --output-file .cafe/evidence/ci/output.md
cafe verification check --output-file .cafe/evidence/qa/output.md
cafe verification run --reuse-from .cafe/evidence/qa/output.md --output-file .cafe/evidence/repeated/output.md
cafe verification reuse --source-output-file .cafe/evidence/qa/output.md --output-file .cafe/evidence/reused/output.md
```

`--add-suite ID` is repeatable and only adds declared optional suites. Mandatory
suites always run in manifest order. Raw replacement commands and narrowing
arguments are rejected in profile mode. `check` and `reuse` accept the same
binding and extras. `reuse` checks evidence without running test suites; incomplete
applicability does not create a passing aggregate. `run --reuse-from` executes
only pending rows. Capability probes may execute during applicability checks.

Profile results distinguish passed, failed and incomplete, with actual per-suite
completion, exit, executed/reused/pending counts and measured durations. A failed
suite leaves later rows pending. Validation errors exit 2; real child failures
retain their code (signals retain the signed result in receipts); invalid or
incomplete checks exit 1. Same clean HEAD/worktree and digest-checked logs remain
necessary. Release identities are diagnostic only. Legacy schema 1/2 evidence
cannot establish a profile claim. Profile evidence does not replace black-box
acceptance, CI policy, or confirmed postmerge/deployed-identity gates.
