# Shared QA and CI verification example

Copy this directory into a fresh repository. Python 3.10+ with timezone data and
an installed CAFE CLI are needed. The fixture has no remote service dependency.

```sh
git init
git add .
git commit -m 'Adopt shared verification profile'
mkdir -p .cafe
cp project-config.yaml .cafe/config.yaml
export TZ=Asia/Taipei
export FROZEN_CLOCK=2026-10-15T15:59:59+00:00
export SEED=656
export BARRIER=notification-before-response
export FAULT=none
# QA consumes the optional project binding.
cafe verification run --output-file .cafe/qa/output.md
cafe verification check --output-file .cafe/qa/output.md
# CI invokes exactly the same repository-owned profile, with no issue state.
cafe verification run --profile-file profiles.json --profile acceptance --output-file .cafe/ci/output.md
# Repeat identical coverage, then reuse checked same-context evidence.
cafe verification run --output-file .cafe/repeat/output.md
cafe verification run --reuse-from .cafe/repeat/output.md --output-file .cafe/cached/output.md
# Only the notification suite depends on the seed.
SEED=657 cafe verification run --reuse-from .cafe/repeat/output.md --output-file .cafe/new-seed/output.md
# Both commands deliberately fail with real nonzero exits.
FAULT=midnight cafe verification run --output-file .cafe/bad-date/output.md
FAULT=notification cafe verification run --output-file .cafe/bad-race/output.md
```

The midnight suite consumes the fixed instant and timezone and checks the local
date transition one second later. The notification suite uses a seeded ID and an
explicit async barrier: it observes a source notification while the save response
is still pending, then releases the response and verifies visible/persisted IDs
and final saved state. There are no wall-clock sleeps. `FAULT` drives executable
negative variants, not receipt labels. Bootstrap/logout are simple representative
existing baseline checks; removing their results cannot establish profile success.

Each suite declares only its relevant file/environment dependencies. `FAULT` is
relevant only to the two negative-variant suites. A suite without sufficient
context declarations may execute but cannot establish reliable reuse. Capability
requirements can be declared as argv probes with observed successful exit/output,
not release-version checks. File inputs may include ignored runtime fixture files
inside the repository: their content can change on the same clean HEAD. Tracked
edits still require a new HEAD, which invalidates all older evidence under the
existing identity boundary. Source receipts/logs remain historical evidence.

These are synthetic verification-contract fixtures, not fixes to kid-todolist or
proof of hidden product semantics (#647). Adoption neither changes CAFE's own CI
nor replaces independent acceptance, CI policy, postmerge or deployed-identity
validation. Non-adopting projects retain legacy command/receipt behavior.

For QA reporting use the following alongside Regression Coverage:

| Suite | Required/selected | Completion/exit | Executed/reused/pending | HEAD and source receipt/log | Context reason | Owner |
| --- | --- | --- | --- | --- | --- | --- |
| bootstrap/logout/midnight/notification | mandatory | observed | observed | checked evidence | applicable or invalidation reason | repository |

| Discovery window | Observation | Count | Source/window | Owner |
| --- | --- | --- | --- | --- |
| After QA | unknown until observed | unknown | no deployed-product observation in this example | product QA |
| After merge | unknown until observed | unknown | no merge/deployment performed by this example | delivery |

Record known defects and failures separately from unknown observations. See
[measurements](../../docs/verification/example-measurements.md) for one bounded
local comparison; no timing threshold or production escape-rate claim is made.
