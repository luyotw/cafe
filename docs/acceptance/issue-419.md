# Issue #419 product acceptance record

## Verdict

- Status: `journey completed to PR with retained acceptance failures`
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
| Child workflow identifier and issue directory | `1f1ced98-dc32-4f55-8124-b36d4ec6f548` / `.cafe/issues/issue421` in the recorded child worktree |
| HumanTask identifier | `24f49da1-2de8-4dc9-af2e-02af16ae44d9` (plan confirmation; the earlier spec clarification was `14ccce49-d9d6-4051-aea4-25bf581f905e`) |
| Child branch | `issue421` at `7276e38f877aa097370c7b832b4643529bc72a4f` |
| Child PR URL | `https://github.com/luyotw/cafe/pull/426` |

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
| Documented install | 3 | 2026-08-24T15:04:52+08:00 | 2026-08-24T15:05:11+08:00 | 19s | After explicit human authorization, run the exact documented `python3 scripts/bootstrap-cafe.py --yes` directly in the unchanged clone and do not relaunch bootstrap in the resumed develop step. | CAFE `0.3.2` installed from `/tmp/cafe-issue419-rMixBg/cafe`; manifest records the versioned environment and `/home/luyotw/.local/bin/cafe`, and the launcher resolves to that environment. | complete | Explicit manual recovery succeeded while both supervised failures remain retained. | agent | Start the supported CLI agent in the same clone with the exact README request. | Direct execution was required because the supervised sandbox could not launch. | 2 | human-authorized direct execution; verified from the install manifest and package metadata | Iteration 003 recovery authorization plus `~/.local/share/cafe-engine/install.json` and installed package metadata. | pass |
| Workflow initialization and kickoff | 1 | 2026-08-24T15:10:46.177308+08:00 | 2026-08-24T15:10:46.429042+08:00 | 0.252s | Start Codex in the same clone under the required supervised-operation boundary and submit `Use CAFE to work on GitHub issue #421 in this repository. Keep our conversation in zh-TW and repository content in en-US.` | Operation `c9f174e324dc4740bcf49ee794f70cb0` failed preflight with `sandbox_user_namespace_unavailable`; Codex was not started and the repository remained unchanged. | stopped | This host cannot create the supervisor's user namespace, so the supported CLI never received the request. | user | Authorize a direct execution of this exact Codex request as a recorded manual recovery, repair the sandbox, or stop the failed journey. | The supervisor rejected the operation before command launch. | 0 | none; operation status was checked once and the command was not relaunched | Iteration 004 operation receipt and recovery-required next action. | fail |
| Workflow initialization and kickoff | 2 | 2026-08-24T15:50:28.505801+08:00 | 2026-08-24T15:50:28.505801+08:00 | bounded start receipt only | After human recovery, execute the exact Codex request directly in the unchanged clone. | The child workflow started as `1f1ced98-dc32-4f55-8124-b36d4ec6f548`, created worktree `.cafe/worktrees/issue421`, and retained origin/base/fixture identity. | complete | Manual kickoff bypassed the unavailable supervised sandbox; the earlier failure remains. | agent | Continue the child workflow without restarting it. | Direct CLI execution was an explicit manual recovery. | 1 | human-authorized exact direct kickoff | Iteration 004 recovery receipt plus child `step_started` and issue-directory receipts. | pass |
| Spec | 1 | 2026-08-24T15:50:28.505801+08:00 | 2026-08-24T15:59:40.998799+08:00 | 9m12s | Run the child spec phase and answer its declared clarification task. | Spec iteration 1 paused for clarification; valid result `73bffec7-c550-4e53-8f81-0c5dcf27b6cc` released the wait, and iteration 2 completed to plan. | complete | Declared clarification cycle completed. | agent | Run plan. | One expected HumanTask pause. | 1 | named human completed the declared result | Child spec artifacts, HumanTask lifecycle, and bounded transition receipts. | pass |
| Plan | 1 | 2026-08-24T16:01:19.376788+08:00 | 2026-08-24T16:08:08.139302+08:00 | 6m49s | Produce the documentation-only plan and stop for confirmation. | Plan completed with all tasks and two invariant-focused integration checks; confirmation HumanTask was materialized. | complete | Plan artifact and declared handoff were produced. | user | Complete the declared output-review result. | One recorded second visit occurred; no completed upstream phase was repeated after the later task release. | 1 | none | Child plan artifact and bounded step receipts. | pass |
| HumanTask creation | 1 | 2026-08-24T16:08:08.188556+08:00 | 2026-08-24T16:08:08.190620+08:00 | 0.002s | Materialize the plan confirmation task through the child workflow. | Durable task `24f49da1-2de8-4dc9-af2e-02af16ae44d9` was assigned to the user with workflow, step, result schema, and `develop` continuation. | paused | Genuine durable child HumanTask created. | user | Discover, inspect, and complete the task. | none | 0 | none | Child `human_tasks.json` and bounded materialization/pause events. | pass |
| HumanTask notification | 1 | 2026-08-24T16:08:08.188556+08:00 | 2026-08-24T16:08:08.190620+08:00 | 0.002s | Let the configured runtime hook deliver through its normal trusted boundary. | No notification hook existed in the unchanged default workflow; no genuine notification or delivery receipt was produced. | failed | Demonstrated default-workflow notification incompatibility. | agent | Retain issue #423 and do not synthesize delivery. | Named human had to use the command path without a notification. | 0 | none | Default configuration, child event types, and issue `https://github.com/luyotw/cafe/issues/423`. | fail |
| HumanTask discovery | 1 | 2026-08-24T16:08:08.190620+08:00 | 2026-08-24T16:08:38.242884+08:00 | 30s window | Named human should run `cafe task ls` from the child repository. | No durable pre-completion `task ls` receipt was retained. The later task store proves existence but cannot substitute for ordered discovery evidence. | failed | Required discovery proof is missing. | agent | Record the evidence failure; do not reconstruct success. | Human action was not receipted at this boundary. | 0 | none | Child HumanTask lifecycle only. | fail |
| HumanTask inspection | 1 | 2026-08-24T16:08:08.190620+08:00 | 2026-08-24T16:08:38.242884+08:00 | 30s window | Named human should run `cafe task inspect 24f49da1-2de8-4dc9-af2e-02af16ae44d9`. | No durable pre-completion inspection receipt was retained; the stored expected-result schema is not proof the human inspected it. | failed | Required inspection proof is missing. | agent | Record the evidence failure; do not reconstruct success. | Human action was not receipted at this boundary. | 0 | none | Child HumanTask lifecycle only. | fail |
| HumanTask completion | 1 | 2026-08-24T16:08:08.188556+08:00 | 2026-08-24T16:08:38.242884+08:00 | 30s | Submit the declared plan confirmation TaskResult. | Result `4693dc52-1e33-4fb4-9d52-c62255bd2968`, source `command`, chose `confirm` and continuation `develop`; task became completed. | complete | Valid declared TaskResult released the wait. | agent | Resume develop once. | No notification receipt accompanied the completion. | 0 | named human completed the task | Child `human_tasks.json` lifecycle and result. | pass |
| Resume | 1 | 2026-08-24T16:08:38.242884+08:00 | 2026-08-24T16:08:38.251726+08:00 | 0.009s | Resume the same workflow after task completion. | The exact wait released once to `develop` under the same workflow ID; spec and plan were not rerun afterward. | complete | Continuation matched the declared target. | agent | Implement the fixture. | none | 0 | none | Child wait state, task result, and develop `step_started` event. | pass |
| Documentation-only develop | 1 | 2026-08-24T16:08:38.251726+08:00 | 2026-08-24T16:14:44.970852+08:00 | 6m07s | Add only the harmless fixture and reconcile its checks. | Commit `7276e38f` adds only `docs/acceptance/fixture-issue-419.md`; no runtime, dependency, playbook, hook, or behavior change. | complete | Planned documentation-only deliverable committed. | agent | Verify and review the unchanged HEAD. | Two develop visits are recorded; the second finalized the same committed change and receipt. | 1 | none | Child commit, diff, plan, and develop summary. | pass |
| Verification | 1 | 2026-08-24T16:11:56.020172+08:00 | 2026-08-24T16:13:41.339124+08:00 | 1m45s | Run repository-defined full verification at clean committed HEAD. | `uv run pytest` passed at unchanged clean `7276e38f`: 2,855 passed, 1 skipped, 1 expected failure; receipt is valid. | complete | Full repository suite passed. | agent | Reuse the receipt in review. | none | 0 | none | Child `develop/iteration_001/verification.json`. | pass |
| Review | 1 | 2026-08-24T16:14:55.534170+08:00 | 2026-08-24T16:16:24.950225+08:00 | 1m29s | Review the committed child change and reusable verification receipt. | Review found no blocking issue and handed the unchanged HEAD to PR. | complete | Requirements, scope, commit, security, dependencies, and tests passed review. | agent | Produce the PR artifact and publish through the registered capability. | none | 0 | none | Child review output and transition receipt. | pass |
| Gap filing | 1 | 2026-08-24T11:13:04+08:00 | 2026-08-24T16:22:48.470117+08:00 | 5h10m evidence window | File one narrow authorized follow-up per demonstrated product gap. | Issues #422 and #423 were filed. The namespace-launch gap has a complete draft but no distinct external-creation authorization, recorded below instead of inferred permission. Missing ordered task-command receipts are an acceptance-execution evidence failure, not a demonstrated product defect. | complete with retained failure | One demonstrated product gap remains unfiled for lack of permission. | user | Authorize filing the namespace-launch draft separately if desired. | External issue creation authority was narrower than the observed third gap. | 0 | none | GitHub issues #422/#423 and bounded gap drafts. | fail |
| PR readiness | 1 | 2026-08-24T16:16:34.572077+08:00 | 2026-08-24T16:20:00+08:00 | 3m25s | Publish through `cafe.pr.publish` and verify the remote object. | PR `https://github.com/luyotw/cafe/pull/426` is open, non-draft, targets `main` from `issue421`, contains commit `7276e38f`, references #421, and reports the documentation-only scope and valid full-suite receipt. | complete | Registered publication produced a human-reviewable PR. | user | Review PR #426. | PR body does not claim the parent journey's failed notification/discovery invariants as passes. | 0 | none | Child publish request plus GitHub PR state verified 2026-08-24. | pass |

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
| Clean repository journey succeeds using documented setup only | Two supervised launches failed and bootstrap/kickoff required explicit direct manual recovery. | fail |
| Every stop exposes status, reason, owner, and exact next action | Ledger and durable HumanTask/wait receipts identify each recorded stop; the unreceipted discovery/inspection boundary cannot prove this for every human action. | fail |
| Genuine HumanTask is created, notified, discovered, inspected, and completed with a valid result | Durable creation/completion and valid result exist, but genuine notification and ordered discovery/inspection receipts do not. | fail |
| Same workflow resumes without repeating completed work | Workflow `1f1ced98-dc32-4f55-8124-b36d4ec6f548` released the plan wait once to develop; spec and plan did not run afterward. | pass |
| Review is actionable and PR is ready for human review | Review passed; PR #426 is open, non-draft, and targets `main` from `issue421`. | pass |
| Record includes elapsed time, friction, recovery, failures, and retries | Ordered ledger retains all observed failed attempts, direct recoveries, phase timing, retries, and evidence limits. | pass |
| Every demonstrated gap has one narrow filed follow-up with minimum sufficient design | #422 and #423 are filed; the namespace-launch gap is drafted but remains unfiled because separate issue-creation permission was not granted. | fail |
| No new architecture or trusted-host capability expansion is introduced | Child diff is one Markdown fixture; active branch changes only the bounded record. | pass |

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
- Depends on: the #419 clean-repository acceptance evidence and the delivered
  HumanTask, ownership, and task-inbox foundations in #396, #397, and #398.
- Non-goals: direct webhook calls, secret copying, or a generic notification
  architecture.
- Filed issue URL: https://github.com/luyotw/cafe/issues/423

### Gap: Supervised sandbox cannot launch when required host namespaces are unavailable

- Evidence and failed invariant: operation `4d0f0debef664679abb54c8d356879a8`
  retained a durable handle but exited before bootstrap with `bwrap: loopback:
  Failed RTM_NEWADDR: Operation not permitted`; later operation
  `c9f174e324dc4740bcf49ee794f70cb0` rejected the Codex kickoff before launch
  with `sandbox_user_namespace_unavailable`.
- User-visible impact: the required supervised boundary cannot run documented
  bootstrap or CLI kickoff commands on this host, even when their inputs and
  writable roots are authorized.
- Recovery and whether it was documented: no recovery was attempted; this
  operation's contract prohibits relaunch, and a direct launch requires an
  explicit human decision and remains a recorded manual recovery.
- Goal: let a supervised command start with an accountable isolation mode when
  the host lacks a required namespace capability.
- Depends on: supervised-operation sandbox launch and host capability probing.
- Scope boundary: preflight detection and an explicit supported launch outcome
  for unavailable user/network namespace capabilities on the current host.
- Non-goals: weakening arbitrary command isolation, changing bootstrap, adding
  a daemon, or silently falling back to unsandboxed execution.
- Minimum sufficient correction: detect the denied operation before reporting
  the child command as started and return an actionable supported outcome, or
  select an already-declared compatible isolation mode.
- Functional Definition of Done: on a host where a required user or network
  namespace is unavailable, a supervised documented command either runs through
  a declared compatible boundary or fails preflight with an actionable reason
  before claiming it started.
- Filed issue URL or explicit permission failure: no distinct authorization was
  granted to create a third external issue; the complete draft above is retained.

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

- Child PR: `https://github.com/luyotw/cafe/pull/426` (open, non-draft,
  `issue421` → `main`)
- Child workflow terminal status: `done` / `workflow_complete` at
  2026-08-24T16:17:46.423020+08:00
- Continuity check: pass for remote, clone, base, fixture issue, workflow,
  worktree, branch, commit, task lifecycle, and PR; no cross-run stage was used.
- Failures retained after retry: lost operation handle; sandbox loopback denial;
  user-namespace kickoff denial; direct install and kickoff recovery; unsupported
  default notification; missing ordered task discovery/inspection receipts; and
  unavailable authorization for the third follow-up issue.
- Overall result: `fail — the unchanged child journey reached a human-reviewable
  PR, but documented-only setup, genuine notification, ordered task discovery /
  inspection, and all-gap-filing criteria were not satisfied`
