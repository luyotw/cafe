# Issue #419 product acceptance record

## Verdict

- Status: `not started`
- Acceptance subject: one disposable child clone and one real fixture issue
- Passing rule: every required stage must pass with one unchanged continuity key.
- Evidence rule: active issue #419 workflow artifacts, simulated boundaries, and
  evidence from another checkout or run cannot satisfy a child-journey stage.

## Continuity key

Fill each value from the child journey before recording a passing stage. A
change to any established value ends that attempt; later evidence must not be
spliced into it.

| Field | Value |
| --- | --- |
| Remote URL | pending |
| Disposable clone root | pending |
| Initial clone commit | pending |
| Journey base commit | pending |
| Stable release tag and commit used for installation | pending |
| Fixture issue number and URL | pending |
| Child workflow identifier and issue directory | pending |
| HumanTask identifier | pending |
| Child branch | pending |
| Child PR URL | pending |

## Preconditions and permission gates

An unavailable gate is a retained failed stage and fails the overall journey;
it is never waived or replaced with a local or synthetic boundary.

| Gate | Required evidence | State | Owner | Exact next action |
| --- | --- | --- | --- | --- |
| Fixture issue and remote mutation | Issue URL, target remote/base, permitted harmless documentation mutation, human owner | pending | pending | Resolve an authorized existing fixture or request permission to create one. |
| Push access | Read-only confirmation of authenticated push access to the fixture remote | pending | pending | Verify access without changing the remote. |
| Notification channel and credential | Configured channel, credential presence without secret contents, destination, named human recipient/completer | pending | pending | Verify configuration and identify the human before cloning. |
| Registered PR capability | Availability and authorization path for `cafe.pr.publish` | pending | pending | Verify without publishing. |
| Host prerequisites | Python, Git, supported CLI agent, and pre-existing `cafe` state | pending | agent | Record versions and disclose pre-existing installation. |

## Ordered journey ledger

Every attempt row records start and end timestamps with timezone, elapsed time,
the documented action, a concise redacted result, status, stop reason, owner,
exact next action, friction, retry count, manual recovery, evidence reference,
and pass/fail. Add retry rows rather than overwriting a failure. Commands are
recorded exactly but credentials, full blackboards, and generated streaming logs
are excluded.

| Stage | Attempt | Start | End | Elapsed | Documented action | Redacted result | Status | Reason | Owner | Exact next action | Friction | Retries | Manual recovery | Evidence | Result |
| --- | ---: | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | ---: | --- | --- | --- |
| Preflight gates | 1 | pending | pending | pending | Verify every gate before creating the clone. | pending | pending | pending | pending | Resolve all gates, then create exactly one disposable clone. | pending | 0 | none | pending | pending |
| Clone and identity | 1 | pending | pending | pending | Create one bounded temporary root and clone the authorized remote once. | pending | pending | pending | pending | Record origin, clean state, release, base commit, root, and timing. | pending | 0 | none | pending | pending |
| Documented install dry run | 1 | pending | pending | pending | Inspect `INSTALL.md` and `scripts/bootstrap-cafe.py`; run the documented dry run at the stable release. | pending | pending | pending | pending | Run the authorized bootstrap only if the dry run and prerequisites pass. | pending | 0 | none | pending | pending |
| Documented install | 1 | pending | pending | pending | Run `python3 scripts/bootstrap-cafe.py --yes` at the recorded stable release. | pending | pending | pending | pending | Record version, launcher, skill sync, mutations, and return to the journey base. | pending | 0 | none | pending | pending |
| Workflow initialization and kickoff | 1 | pending | pending | pending | Start the supported CLI agent in the same repository, submit the exact README issue request, confirm the kickoff once, and initialize CAFE. | pending | pending | pending | pending | Record generated project files, workflow identity, status, owner, and next action. | pending | 0 | none | pending | pending |
| Spec | 1 | pending | pending | pending | Run the child spec phase through documented status and task surfaces. | pending | pending | pending | pending | Follow the reported next action without repeating completed work. | pending | 0 | none | pending | pending |
| Plan | 1 | pending | pending | pending | Run the child plan phase through documented status and task surfaces. | pending | pending | pending | pending | Follow the reported next action without repeating completed work. | pending | 0 | none | pending | pending |
| HumanTask creation | 1 | pending | pending | pending | Let the child workflow materialize a durable confirmation task. | pending | pending | pending | pending | Correlate its stable identifier to the child repository and workflow. | pending | 0 | none | pending | pending |
| HumanTask notification | 1 | pending | pending | pending | Let the configured runtime hook deliver through its normal trusted boundary. | pending | pending | pending | pending | Named human follows the notified completion path. | pending | 0 | none | pending | pending |
| HumanTask discovery | 1 | pending | pending | pending | Named human runs `cafe task ls` from the child repository. | pending | pending | pending | pending | Inspect the same stable task identifier. | pending | 0 | none | pending | pending |
| HumanTask inspection | 1 | pending | pending | pending | Named human runs `cafe task inspect <task-id>` and checks schema and continuation. | pending | pending | pending | pending | Submit one valid declared TaskResult. | pending | 0 | none | pending | pending |
| HumanTask completion | 1 | pending | pending | pending | Named human runs the declared `cafe task complete` path. | pending | pending | pending | pending | Resume the same child workflow once. | pending | 0 | none | pending | pending |
| Resume | 1 | pending | pending | pending | Resume the same workflow and verify the wait releases once. | pending | pending | pending | pending | Confirm completed spec/plan phases did not repeat. | pending | 0 | none | pending | pending |
| Documentation-only develop | 1 | pending | pending | pending | Make only the fixture issue's harmless documentation change and bounded evidence updates. | pending | pending | pending | pending | Commit through the child workflow's normal process. | pending | 0 | none | pending | pending |
| Verification | 1 | pending | pending | pending | Run repository-defined verification at committed, clean child HEAD and create its receipt. | pending | pending | pending | pending | Continue the unchanged HEAD to review. | pending | 0 | none | pending | pending |
| Review | 1 | pending | pending | pending | Complete the child default review and retain findings and retries. | pending | pending | pending | pending | Use only the authorized registered PR capability. | pending | 0 | none | pending | pending |
| Gap filing | 1 | pending | pending | pending | File one narrow authorized follow-up for each demonstrated gap. | pending | pending | pending | pending | Record issue links or the explicit filing-permission failure. | pending | 0 | none | pending | pending |
| PR readiness | 1 | pending | pending | pending | Publish through `cafe.pr.publish` and verify open ready-for-review state, branch/base, issue reference, limitations, and gap links. | pending | pending | pending | pending | Hand the PR to a human reviewer. | pending | 0 | none | pending | pending |

## Stage invariants

- Repository origin, clone root, commits, fixture issue, workflow, task, branch,
  and PR remain attributable to the continuity key.
- Every stop reports status, reason, owner, and exact next action.
- Missing permissions or credentials are explicit failures; mocks, direct hook
  calls, manual state edits, and another run cannot turn them into passes.
- Completed phases are not repeated after HumanTask completion and resume.
- Undocumented commands, edits, retries, recovery, or repository replacement
  fail the clean-setup invariant and remain visible in the ledger.
- Active issue #419 may arrange gates and copy bounded receipts only; it supplies
  no child-stage proof.

## Acceptance criteria reconciliation

| Criterion | Evidence | Result |
| --- | --- | --- |
| Clean repository journey succeeds using documented setup only | pending | pending |
| Every stop exposes status, reason, owner, and exact next action | pending | pending |
| Genuine HumanTask is created, notified, discovered, inspected, and completed with a valid result | pending | pending |
| Same workflow resumes without repeating completed work | pending | pending |
| Review is actionable and PR is ready for human review | pending | pending |
| Record includes elapsed time, friction, recovery, failures, and retries | pending | pending |
| Every demonstrated gap has one narrow filed follow-up with minimum sufficient design | pending | pending |
| No new architecture or trusted-host capability expansion is introduced | pending | pending |

## Demonstrated gaps

Create entries only for observed failures or undocumented recovery. Each entry
must be independently acceptable and non-overlapping.

No gaps recorded before execution.

<!--
### Gap: <title>

- Evidence and failed invariant:
- User-visible impact:
- Recovery and whether it was documented:
- Goal:
- Depends on:
- Scope boundary:
- Non-goals:
- Minimum sufficient correction:
- Functional Definition of Done:
- Filed issue URL or explicit permission failure:
-->

## Final reconciliation

- Child PR: pending
- Child workflow terminal status: pending
- Continuity check: pending
- Failures retained after retry: pending
- Overall result: `not started`
