# Durable human-task runtime

CAFE persists user-owned handoffs as workflow-local records and exposes them
through the repository task inbox. The built-in `standard` workflow also has
one optional, fixed-destination [Slack notification path](human-task-slack-notifications.md)
for newly materialized tasks. It does not provide scheduling, reminders,
bidirectional Slack interaction, or a general task-management service.

## Files and ownership

Each workflow instance stores its durable state below `.cafe/issues/<issue>/`:

| File | Schema | Owner | Purpose |
| --- | --- | --- | --- |
| `blackboard.json` | 2 | `BlackboardStore` | The workflow position, baton, and stable `workflow_id`. |
| `human_tasks.json` | 1 | `HumanTaskRecordStore` | HumanTask, Assignment, WaitState, TaskResult, and lifecycle evidence. |

`human_tasks.json` is written atomically. It is runtime state and should not be
edited by hand while a workflow is active.

## Record lifecycle

When the runtime reaches a declared user-owned handoff, it resolves the same
#345 policy/binding used by the existing handoff UI, then creates or reuses a
single task identified by its workflow id, producing step, iteration, trigger,
and policy id. The task snapshots the prompt, expected-result contract, and
declared continuations needed to understand the pause after a restart.

Each task has:

- an `Assignment` (currently assignee type `user` and optional identity);
- a `WaitState`, which records the workflow/task correlation and pause reason;
- at most one `TaskResult`, containing the already-validated response, source,
  and completion time; and
- append-only lifecycle events for `created`, `completed`, `rejected`,
  `cancelled`, and `configuration_error` outcomes.

Invalid responses retain the pending wait and append rejection evidence. A
cancelled or completed task cannot create another result. Replaying a completed
request leaves the original result intact and never emits another continuation.
Only the transaction that creates a new task is eligible for the optional
standard-workflow Slack attempt; task recovery after a restart does not send a
duplicate notification.

## Completion and correlation

The #345 `HumanTaskPolicy` and payload validator remain the authority for
validating a human response and selecting a declared continuation. Durable
records add a second guard after that validation:

1. The active WaitState must match the current `workflow_id`, source step,
   trigger, and policy.
2. A new interactive or JSON response for a durable task must include a
   `human_task_id` that matches that active task. After an interruption that
   occurs after result persistence, interactive recovery reuses the matching
   stored result and task id without asking the participant to resubmit it.
3. The selected continuation must be one captured by that task's handoff,
   unless a supervising user explicitly supplies `cafe task complete
   --handoff-to <phase>`. That override may select any real phase in the owning
   playbook, while the original HumanTask response remains mandatory and valid.
4. CAFE persists the one TaskResult before it updates the baton.

This prevents a result from another workflow, a stale/cancelled task, a
duplicate completion, or a changed continuation from advancing the workflow.
For an explicit supervisor override, the TaskResult and Blackboard completion
event retain both the declared and selected continuations, and an optional work
report is delivered to the selected phase.

## Inspect and submit a task snapshot

Run `cafe task inspect <task-id> --json` from the repository. `data.task`
contains the existing task contract and identifies the workflow, source step,
and iteration. `data.snapshot` has `schema_version: 1`, `status`, and `version`.
Treat `version` as an opaque value and keep it alongside the data you display.

| Snapshot field | Contract |
| --- | --- |
| `status` | `available` gives a usable version. `unavailable` gives a null version when the task is stale, terminal, or missing required data. `not_applicable` gives a null version for tasks with a separate approval protocol. |
| `questions` | When `available`, `items` is an ordered array of `{id, title, options, multi_select}` for a task that uses XML questions. Inline questions on other tasks are in `data.task.expected_result`. |
| `review_artifact` | When `available`, contains the issue-relative `path`, UTF-8 `text`, `sha256` of the file bytes, and `registered_version` (an integer if registered, otherwise null). It describes the producing iteration's `output.md` for output review and no-change decisions. |
| `reason` | When the task has no inspectable context, this code describes the reason and the two component fields may be absent. |

Each component reports `available`, `unavailable`, or `not_applicable`. CAFE
reads `questions.xml` and `output.md` from the task's producing iteration.
Each file must be a regular file with a single link and at most 1 MiB.
A registered review artifact must match the producing step and path, along
with its recorded content digest when present. Missing, invalid, and unsafe
inputs make the relevant component unavailable. Capability and delivery
approvals use their existing review contracts.

To submit a versioned reply, add `snapshot_version` to the task's JSON result
and pass it to `cafe task complete <task-id> --result` or `--result-file`.
You can also include `workflow_id` from `data.task`. CAFE checks the snapshot
under the durable task transaction and validates the response with the normal
HumanTask policy. Omitting `snapshot_version` uses the existing response path.

The JSON error envelope includes `error.code` and `error.recovery`. A rejected
reply leaves the workflow paused if this is still its active handoff.

| Error code | Client action |
| --- | --- |
| `stale_snapshot` | Inspect again and display the refreshed version before asking for a new reply. |
| `snapshot_unavailable` | Wait for the owning workflow to restore the task data, then inspect again. Use the declared approval protocol for special approval tasks. |
| `stale_task`, `task_not_pending` | List pending tasks and inspect the current task. A replay cannot advance the workflow again. |
| `invalid_response` | Correct the result using the declared response contract. |

For a pending spec clarification task whose question IDs are `scope` and
`platforms`, run the following commands with its ID in `TASK_ID`. The first
command captures the version and the displayed data in a single response.
After reviewing the displayed questions, supply the participant's answers.

```bash
cafe task inspect "$TASK_ID" --json > task.json
jq -e '.data.snapshot | select(.status == "available") | .questions.items' task.json
jq -e 'select(.data.snapshot.questions.status == "available") |
       {snapshot_version: .data.snapshot.version, workflow_id: .data.task.workflow_id,
        answers: {scope: "Small", platforms: ["CLI", "API"]}}' task.json > reply.json
cafe task complete "$TASK_ID" --result-file reply.json --no-resume --json
```

For an output review, display `.data.snapshot.review_artifact.text` and use the
task's declared `decision` in place of `answers`. To reproduce the clarification,
review, stale submission, and replay journeys with a local test workflow, run
`python -m pytest tests/integration/test_task_inbox_workflow.py -k external --no-cov`.

## Migration and compatibility

Blackboard schema 1 did not contain a workflow id. On first load, CAFE assigns
and persists a new stable `workflow_id` as schema 2. This migration does not
create a HumanTask for a workflow that was already paused.

An in-progress #345 handoff with no `human_tasks.json` remains taskless and
uses the established interactive and `--user-input` completion paths. New
user-owned handoffs materialize durable records normally.

`human_tasks.json` currently supports only schema version 1. A missing file is
the supported legacy case. A malformed file or an unknown future schema fails
closed and is not rewritten, so it cannot silently authorize a continuation.
