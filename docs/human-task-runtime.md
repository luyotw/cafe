# Durable human-task runtime

CAFE persists user-owned handoffs as workflow-local records. This is a runtime
foundation only: it does not provide an inbox, scheduling, reminders,
notifications, or a task-management UI.

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

## Completion and correlation

The #345 `HumanTaskPolicy` and payload validator remain the authority for
validating a human response and selecting a declared continuation. Durable
records add a second guard after that validation:

1. The active WaitState must match the current `workflow_id`, source step,
   trigger, and policy.
2. When present, `human_task_id` in the interactive or JSON payload must match
   that active task.
3. The selected continuation must be one captured by that task's handoff.
4. CAFE persists the one TaskResult before it updates the baton.

This prevents a result from another workflow, a stale/cancelled task, a
duplicate completion, or a changed continuation from advancing the workflow.

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
