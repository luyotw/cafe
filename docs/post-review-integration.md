# Verified post-review integration

Select `standard-qa-integrate` when completion must establish that accepted changes
reached an explicitly confirmed destination. Existing review-only playbooks retain
their publication/review behavior and never claim verified integration.

```sh
cafe prepare delivery --playbook standard-qa-integrate
cafe workflow --issue delivery --execute
```

Follow the normal specification, plan, development, review and QA tasks. The `pr`
step preserves the existing `pr.auto_create` choice: it prepares local review material
or publishes through the existing authorized capability. Its accepted decisions
remain `create_follow_up` and `continue_without_issue`; `fix_now` retains correction.
A follow-up choice records the existing follow-up request and does not create an
issue or authorize merging. Acceptance leads to destination selection, not `done`.

## Select and confirm the destination

Use the reviewed repository/worktree's absolute path for local delivery. CAFE freezes
the approved commit from the declared review artifact; users do not supply a new SHA.
The local target must be a short local branch name distinct from the feature branch.

```sh
cafe integration select --issue delivery --target local_branch \
  --repository /absolute/path/to/reviewed/worktree \
  --feature-branch delivery --target-branch main --json
```

For GitHub, select the repository and published PR associated with that accepted
review's declared delivery artifact and publication receipt:

```sh
cafe integration select --issue delivery --target github_pr \
  --repository owner/repository --pr 123 --target-branch main --json
```

Neither command confirms its own candidate. Inspect the returned confirmation task:

```sh
cafe task ls --issue delivery
cafe task inspect CONFIRMATION_TASK_ID --json
```

The bundled destination policy expects this result file, with the real task id:

```json
{"task":"integration-destination","human_task_id":"CONFIRMATION_TASK_ID","decision":"confirm"}
```

```sh
cafe task complete CONFIRMATION_TASK_ID --result-file confirmation.json --no-resume
cafe workflow --issue delivery --execute
cafe task ls --issue delivery
cafe task inspect ACTION_TASK_ID --json
```

To change a candidate, run `integration select` again and confirm its new task. The
old selection, reports and attempts remain auditable, but old evidence cannot qualify
for the new destination. A different repository/feature or reviewed source requires
correction and renewed review. Completed delivery is immutable; another destination
belongs to a new workflow.

## Human action, reporting and verification

The person performs the GitHub merge or local integration outside CAFE, using the
exact task identities. CAFE never merges, fetches, pushes, changes checkout, deploys,
or removes a worktree during this journey. Preserve ordinary repository controls
when performing that human work.

Report through the existing task result mechanism:

```json
{
  "task":"human-integration",
  "human_task_id":"ACTION_TASK_ID",
  "decision":"performed",
  "work_report":{
    "summary":"Integrated the reviewed source into the confirmed destination",
    "outcome":"Human integration performed",
    "evidence":["https://github.com/owner/repository/pull/123"]
  }
}
```

`already_performed` reports an earlier human action. `blocked` records inability or
conflict, with the same optional `work_report` structure. Evidence supplied by the
person is a report, not destination proof. Policy/task names in these JSON examples
are for the bundled playbook; custom workflows use the names returned by inspection.

```sh
cafe task complete ACTION_TASK_ID --result-file report.json --no-resume
cafe integration status --issue delivery --json
cafe integration verify --issue delivery --json
cafe workflow --issue delivery --execute
```

With `--no-resume`, status remains `reported_pending_verification`. Verification
returns exit 1 with an actionable result when proof is negative/unavailable; success
returns exit 0 and persists proof. `verify` leaves completion to the guarded workflow
continuation. `status` reads records/catalogs without subprocesses, network access,
reconciliation or state writes. `cafe status` also includes the integration state
when an integration record exists.

| Target | Qualifying evidence | Incomplete examples |
| --- | --- | --- |
| `github_pr` | Exact repository/PR, approved head SHA, declared base, merged state and valid merge commit. GitHub merge, squash and rebase methods qualify through PR facts. | Open or closed-unmerged PR; wrong repo/number/head/base; absent merge commit; malformed or unavailable API response. |
| `local_branch` | Actual `refs/heads/<target>` HEAD equals or descends from the approved source, with a stable destination observation. | Missing source/target, unrelated or rewritten history, feature checkout HEAD, only a remote-tracking ref, squash/cherry-pick similarity. |

Missing objects never trigger fetch. The local verifier needs no GitHub tools or
credentials. GitHub uses existing `gh` read access; unavailable inspection remains
incomplete and uses the normal human assistance boundary. No declaration grants
new executable or host authority. Evidence describes the observed time; completed
workflows are not continuously monitored.

## Conflicts, already-integrated delivery and restart

For a conflict, submit `blocked`. Status preserves the human report and asks for
corrective human work. Resolve externally, then retry `integration verify` against
the same selection and resume the workflow. If resolution changes the reviewed
source artifact or feature revision, normal workflow recovery routes to the declared
correction step and requires renewed review; it does not silently approve the new
revision. Do not edit `integration.json` or `human_tasks.json` to clear a conflict.

If integration happened before reporting, run the same workflow again, or use
`integration verify` then resume. CAFE inspects the confirmed destination, records
an explicitly absent report, and cancels the now-unnecessary pending action task
without fabricating a HumanTask result or asking for another merge.

After interruption following task creation, report persistence or proof persistence,
run `cafe workflow --issue delivery --execute`. Durable task/result ids are reconciled
and only read-only inspection is retried. Failure to save proof prevents completion.
Before interrupted completion, CAFE rechecks the destination: ordinary local target
advancement retaining ancestry qualifies; lost ancestry or a changed PR head/base
blocks. Repeated resume after valid durable `done` preserves its existing completion
association without duplicate tasks or further destination inspection.

## Custom declaration

The optional top-level declaration names review/source/delivery/selection identities.
Producer and action identities are derived from the ordinary graph:

```yaml
terminal_prerequisite: verified_delivery
integration:
  review_step: pr
  review_task: local-review
  accepted_decisions: [create_follow_up, continue_without_issue]
  source_artifact: workspace
  delivery_artifact: pr_result
  selection_step: destination
  selection_task: integration-destination
```

See `src/cafe/data/playbooks/standard-qa-integrate.yaml` for complete bindings and
`src/cafe/data/skills/cafe-integrate/SKILL.md` for English/Traditional Chinese policies.
Accepted review outcomes point to the selection step; `confirm` points to action;
`performed`, `already_performed` and `blocked` point to an auto-owned step with
`automatic: {executor: verify_delivery, inputs: {}}`. Its ordinary `on` routes map
`workflow_complete` to `_done`, `need_permission` to human action and
`manual_handoff` to correction. The human action step declares
`resume_intent: await_agent` and `on: {await_agent: <verifier-step>}` so restart
can inspect completed external work without fabricating a report. Review,
selection and action bindings use the closed `context_contract` IDs
`reviewed_delivery`, `delivery_destination` and `delivery_action` respectively.
Selection and action remain human-owned. No declaration registers executable code.
The fixed verifier receives only issue path, workflow ID and step from its host,
reloads the effective catalog, performs read-only inspection and persists immutable
proof. Ordinary owner lifecycle publication consumes that proof once, with its
workflow sequence, under the shared terminal prerequisite and publication fence.
The terminal gate itself performs no inspection. An intervening transition, restart,
retarget or source/proof drift discards freshness and re-enters the declared verifier.

Equivalent custom step, artifact,
policy and accepted-decision names work through the same catalog/task/runtime paths.
The source artifact is the versioned WorkspaceArtifact from its named producer,
and GitHub publication is matched to the named prepared artifact's receipt.
Per-issue overrides remain limited to existing attempt-limit settings.

## Independent QA walkthrough

Independent QA must record separate acceptance observations for each target's
success, failure, conflict, already-integrated and recovery paths. Automated test
results below are implementation evidence, not independent acceptance or live GitHub
merge evidence. Use separate scratch repositories; retain CLI JSON, task/result ids,
proof timestamps, actual target identities and completion association.

For an isolated pre-review fixture with real Git history and a strict custom catalog:

```sh
PYTHONPATH=src .venv/bin/python -m tests.integration.integration_fixture \
  /tmp/cafe-integration-qa-local
```

For GitHub process-boundary modeling, add `--github-fixture`. The fixture prints the
review task and source identity. Enter the printed repository and invoke the actual
public app in separate processes with the source checkout on `PYTHONPATH`:

```sh
export CAFE_SKIP_GLOBAL_SKILL_SYNC=1
python -c 'from cafe.ui.cli import app; app()' task ls --issue delivery --json
```

Complete its review using `task: judge`, `decision: ship` and the printed
`human_task_id`. Use the normal commands above with custom policy names `choose`
for confirmation and `human-delivery` for reporting, feature `feature`, issue
`delivery`, repository equal to the printed scratch path. These fixtures represent
prepared agent output and an existing publication receipt; no live publication runs.

For local success, a human moves scratch `main` to the printed approved source
(using ordinary Git outside the app), then verifies/resumes. Keep `main` at the
baseline for failure. For GitHub, prepend the printed fixture directory to `PATH`;
its `gh` accepts only `--version` and the exact read-only PR API request. Edit its
`pr.json` as the modeled external human/service: set `state: closed`, `merged: true`
and a full `merge_commit_sha` for success, or leave it open/wrong for failure.
`requests.jsonl` retains the application API requests. Label these observations
fixture-backed; they do not establish a live remote merge.

For conflict, report `blocked`, restart, inspect the preserved report, resolve the
external fixture, verify, and resume. Repeat with a changed source to observe the
correction/re-review boundary. For already-integrated delivery, perform the external
fixture action after confirmation without reporting, then resume and verify report
absence. For recovery, restart between task creation/report/verification/completion;
retain ids, rewrite the local target or GitHub base before resume to ensure old proof
fails, and retry with corrected facts. Persistence/task-association fault cases are
reproducible in the focused tests:

```sh
PYTHONPATH=src .venv/bin/python -m pytest \
  tests/integration/test_post_review_integration.py \
  tests/integration/test_post_review_integration_cli.py -q --no-cov
```

Record each of the ten target/path observations in the normal QA output, including
human integration ownership, durable proof before completion and preserved
publication/review-only compatibility. Do not infer QA acceptance from this document.
