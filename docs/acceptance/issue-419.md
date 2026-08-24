# Issue #419 product acceptance record

## Verdict

- Status: `failed setup retry — supervised sandbox could not launch bootstrap`
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
| Remote URL | `https://github.com/luyotw/cafe.git` |
| Disposable clone root | `/tmp/cafe-issue419-rMixBg/cafe` |
| Initial clone commit | `d5f25af7cbfd473d29733c08508e1c9ad891851c` |
| Journey base commit | `d5f25af7cbfd473d29733c08508e1c9ad891851c` (`main`) |
| Stable release tag and commit used for installation | `v0.3.2` / `d5f25af7cbfd473d29733c08508e1c9ad891851c` |
| Fixture issue number and URL | `#421` / `https://github.com/luyotw/cafe/issues/421` |
| Child workflow identifier and issue directory | pending |
| HumanTask identifier | pending |
| Child branch | pending |
| Child PR URL | pending |

## Preconditions and permission gates

An unavailable gate is a retained failed stage and fails the overall journey;
it is never waived or replaced with a local or synthetic boundary.

| Gate | Required evidence | State | Owner | Exact next action |
| --- | --- | --- | --- | --- |
| Fixture issue and remote mutation | Issue URL, target remote/base, permitted harmless documentation mutation, human owner | pass | agent | Dedicated documentation-only fixture issue `#421` was created on `luyotw/cafe`; `luyotw` authorized its branch and CAFE-published PR. |
| Push access | Read-only confirmation of authenticated push access to the fixture remote | pass | agent | Authenticated `luyotw` access and explicit fixture branch/PR authorization are recorded; defer the push until child develop. |
| Notification channel and credential | Configured channel, credential presence without secret contents, destination, named human recipient/completer | incompatible | agent | The supported `openfun-dataset.yaml` reference uses `after_execute` `notify-slack.sh` hooks and the credential file has mode 600, but the child default workflow has no script notification hook and cannot consume that project-local reference without an unauthorized playbook/script copy. Retain the incompatibility; never invoke the script directly. |
| Registered PR capability | Availability and authorization path for `cafe.pr.publish` | pass | agent | Package registration exists with GitHub destinations, `gh` credential, medium risk, and allow/not-required policy; publication remains deferred to the child PR stage. |
| Host prerequisites | Python, Git, supported CLI agent, and pre-existing `cafe` state | pass | agent | Python 3.12.2, Git 2.43.0, Codex CLI 0.146.0, and a pre-existing `cafe` launcher at `/home/luyotw/anaconda3/bin/cafe` were recorded. The existing installation is not install-stage evidence. |

## Ordered journey ledger

Every attempt row records start and end timestamps with timezone, elapsed time,
the documented action, a concise redacted result, status, stop reason, owner,
exact next action, friction, retry count, manual recovery, evidence reference,
and pass/fail. Add retry rows rather than overwriting a failure. Commands are
recorded exactly but credentials, full blackboards, and generated streaming logs
are excluded.

| Stage | Attempt | Start | End | Elapsed | Documented action | Redacted result | Status | Reason | Owner | Exact next action | Friction | Retries | Manual recovery | Evidence | Result |
| --- | ---: | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | ---: | --- | --- | --- |
| Preflight gates | 1 | 2026-08-24T11:03:24+08:00 | 2026-08-24T11:04:59+08:00 | 1m35s | Verify every gate before creating the clone. | GitHub repository and #419 are readable; `gh` is authenticated; package PR capability and webhook credential file exist; default playbook contains no notification hook; fixture authorization and named human are absent. | stopped | Required real boundaries are not fully authorized/configured. | user | Answer the develop HumanTask with fixture/install authorization and an existing documented notification configuration plus named completer. | Default documented workflow does not configure the available notification script. | 0 | none; clone was not created | Read-only command receipt in active develop iteration and `src/cafe/data/playbooks/default.yaml`; no active-workflow artifact counts as child evidence. | fail |
| Preflight gates | 2 | 2026-08-24T11:11:04+08:00 | 2026-08-24T11:12:09+08:00 | 1m05s | Apply the completed HumanTask authorization and recheck the real boundaries. | Fixture issue/branch/CAFE PR, documented user bootstrap, named receiver/completer `luyotw`, and the credential boundary are authorized. The supplied hook is project-local to `open-forest-scripts`; default CAFE has no equivalent script hook. | failed | Reference notification configuration is incompatible with the unchanged child default workflow. | agent | Preserve the incompatibility and do not copy or invoke the hook; continue only until the next independent blocker. | The existing supported notification configuration is not portable to the default child playbook. | 1 | none | User input, fixture `#421`, child/default playbook comparison; secret not read. | fail |
| Clone and identity | 1 | 2026-08-24T11:12:09+08:00 | 2026-08-24T11:12:11+08:00 | 2s | Create one bounded temporary root and clone the authorized remote once. | Clean HTTPS clone at `/tmp/cafe-issue419-rMixBg/cafe`; origin, `main`, initial/base commit, and stable tag all resolve to the recorded continuity values. | complete | Authorized clone and identity established. | agent | Inspect the stable release install files and run the dry run. | none | 0 | none | Git clone and identity command receipt. | pass |
| Documented install dry run | 1 | 2026-08-24T11:12:11+08:00 | 2026-08-24T11:12:37+08:00 | 26s | Inspect `INSTALL.md` and `scripts/bootstrap-cafe.py`; run the documented dry run at the stable release. | Python 3.12.2, Git 2.43.0, Codex 0.146.0; dry run proposed a versioned environment, `~/.local/bin/cafe`, and global skill sync without system or shell-profile changes. | complete | Documented dry run passed. | agent | Run the authorized non-interactive bootstrap once. | Existing unrelated CAFE launcher was disclosed. | 0 | none | Bounded dry-run receipt. | pass |
| Documented install | 1 | 2026-08-24T11:12:58+08:00 | 2026-08-24T11:13:04+08:00 | 6s | Run `python3 scripts/bootstrap-cafe.py --yes` at the recorded stable release under the required supervised-operation boundary. | Operation `b735688f2edd4eabb066e8c0618fff07` reported `running`, then the first status check reported `lost` / `operation_handle_missing`; no bootstrap process remained and `~/.local/bin/cafe` was absent. | stopped | The supervised operation lost its handle before producing a terminal command receipt. | user | Start a new develop iteration if a documented retry is desired; this iteration's operation contract prohibits relaunching the same command. | The documented command itself never produced an accountable result because its supervisor lost the operation. | 0 | none; no direct fallback or second launch attempted | Operation receipt and follow-up `https://github.com/luyotw/cafe/issues/422`. | fail |
| Documented install | 2 | 2026-08-24T13:26:11.426338+08:00 | 2026-08-24T13:26:11.818356+08:00 | 0.392s | After driver-authorized recovery for issue #422, retry `python3 scripts/bootstrap-cafe.py --yes` at the same clone and release through a new supervised operation. | Operation `4d0f0debef664679abb54c8d356879a8` retained its handle and terminal receipt, but its sandbox exited 1 before bootstrap started: `bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`. | stopped | The supervised sandbox cannot configure loopback in this host boundary; the bootstrap command produced no output and did not run. | user | Choose whether to repair the sandbox and authorize another supervised retry, explicitly authorize a direct documented-command recovery, or stop the failed journey. | Handle durability is fixed, but the documented install remains blocked by the supervisor's platform sandbox. | 1 | none; the failed operation was not relaunched | Iteration 003 operation receipt and bounded stderr; follow-up draft below. | fail |
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

### Gap: Keep supervised operation handles durable through bootstrap launch

- Evidence and failed invariant: operation `b735688f2edd4eabb066e8c0618fff07`
  changed from `running` to `lost` / `operation_handle_missing` before the
  bootstrap could be accounted for.
- User-visible impact: the documented installation could not be completed or
  safely retried in the same develop iteration.
- Recovery and whether it was documented: no recovery was attempted; the phase
  contract explicitly forbids relaunching the same long-running command.
- Goal: retain a queryable handle through a terminal receipt or report a
  concrete launch failure before claiming the operation is running.
- Depends on: the supervised-operation launch and status boundary.
- Scope boundary: handle durability and actionable launch failure reporting.
- Non-goals: bootstrap changes, daemons, arbitrary retries, or broader sandbox
  authority.
- Minimum sufficient correction: atomically publish and retain the operation
  handle until a durable terminal receipt exists.
- Functional Definition of Done: `operation run` followed by `operation status`
  reaches a terminal receipt or specific launch failure and never loses an
  already reported running operation.
- Filed issue URL: https://github.com/luyotw/cafe/issues/422

### Gap: Default workflow cannot use the supplied supported Slack hook in place

- Evidence and failed invariant: the supported reference configures
  project-local `notify-slack.sh` hooks in `openfun-dataset.yaml`, while the
  unchanged child `default.yaml` has no script notification hook or matching
  skill-local implementation.
- User-visible impact: a default new-user workflow cannot produce the required
  genuine Slack notification using the supplied existing configuration without
  copying project configuration or calling the script directly.
- Recovery and whether it was documented: no recovery was attempted because
  both copying the hook and direct invocation are explicitly forbidden.
- Goal: provide one documented supported notification configuration for the
  default workflow without project-local copying or direct script invocation.
- Non-goals: direct webhook calls, secret copying, or a generic notification
  architecture.
- Filed issue URL: https://github.com/luyotw/cafe/issues/423

### Gap: Supervised sandbox cannot launch on a host that denies loopback setup

- Evidence and failed invariant: operation `4d0f0debef664679abb54c8d356879a8`
  retained a durable handle but exited before bootstrap with `bwrap: loopback:
  Failed RTM_NEWADDR: Operation not permitted`.
- User-visible impact: the required supervised boundary cannot run the
  documented bootstrap on this host, even though the bootstrap inputs and
  writable roots are authorized.
- Recovery and whether it was documented: no recovery was attempted; this
  operation's contract prohibits relaunch, and a direct launch requires an
  explicit human decision and remains a recorded manual recovery.
- Goal: let a supervised command start with an accountable isolation mode when
  the host denies bubblewrap loopback configuration.
- Depends on: supervised-operation sandbox launch and host capability probing.
- Scope boundary: preflight detection and an explicit supported launch outcome
  for this one denied loopback capability.
- Non-goals: weakening arbitrary command isolation, changing bootstrap, adding
  a daemon, or silently falling back to unsandboxed execution.
- Minimum sufficient correction: detect the denied operation before reporting
  the child command as started and return an actionable supported outcome, or
  select an already-declared compatible isolation mode.
- Functional Definition of Done: on a host where `RTM_NEWADDR` is denied, a
  supervised documented command either runs through a declared compatible
  boundary or fails preflight with an actionable reason before claiming it
  started.
- Filed issue URL or explicit permission failure: pending authorization.

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
- Overall result: `failed setup retry; awaiting a human recovery decision after the supervised sandbox launch failure`
