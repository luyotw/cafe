# Manager Supervision And Recovery

Read this reference while workflow work is active or when execution pauses,
times out, becomes interrupted, or appears stale. Supervision policy belongs to
the Manager. Existing CAFE status, task, handoff, process, and output surfaces are
the evidence the Manager uses; do not require or create a failure fingerprint,
recurrence counter, or additional runtime evidence schema.

## Observe active work

Inspect `cafe status`, the relevant `cafe show` surfaces, the active HumanTask,
baton/handoff state, and bounded process output. Establish the current workflow,
phase, iteration, task, operating mode, process owner, and applicable authority
from what those surfaces currently show. Do not act from chat memory alone or
invent hidden state that CAFE does not report.

A transport yield, live process, filesystem change, heartbeat, or partial output
is only one observation. None proves phase completion; follow the graph's
handoff and terminal state. Do not read unbounded provider logs merely to watch
execution.

The Manager judges whether failures are the same in substance from their visible
meaning, failed boundary, and execution context. Exact wording need not match.
When the visible information is insufficient or conflicting, treat the state as
ambiguous and diagnose it instead of manufacturing certainty.

When reporting a callback or runtime failure to the user, translate internal
field names and error codes into the failed operation, its visible effect, and
one usable next action. For a saved-state format mismatch, explain that the
current CAFE version could not read older workflow state and identify whether
only the automatic conversation notification failed or the workflow itself
paused, based on fresh `cafe status` and task evidence. A callback failure alone
does not prove the worker stopped. Keep raw field names in diagnostic evidence,
not as the user's instruction; never ask the user to edit state files. If the
workflow is still running, report that the Manager can inspect it when the user
returns. If a task is pending, present its declared choices in plain language.

## Non-intervention envelope

The Manager remains passive while every applicable condition is demonstrably
true:

- the visible workflow, phase, iteration, continuation/session, and worker state
  are current and mutually consistent;
- no concurrent or duplicate phase execution or resume is apparent;
- execution remains within its confirmed bounded wait;
- visible progress remains current, or the wait interval has not elapsed;
- repeated attempts still have a concrete reason to produce a different result;
- artifacts, checklist, baton, HumanTask, and reported execution state do not
  conflict;
- scope, permission, capability, model chain, and Delivery Contract remain
  unchanged;
- no scheduled proactive-review or confirmation boundary is due; and
- the continuous workflow worker still owns ordinary advancement.

Inside the envelope, do not invoke `cafe chat` to watch progress, inspect
implementation code or diffs, run phase work, restart/resume/select a step,
mutate tasks/artifacts/blackboard/baton/model/authority, or add a review or
confirmation gate.

## Classify leaving the envelope

Leaving the envelope requires a fresh inspection. Preserve every visible
blocker, choose the highest-priority applicable action below, then inspect again.
No observation automatically authorizes chat, retry, command execution, model
changes, or workflow-state mutation.

Use the read-only flag for an authorized phase-agent diagnostic consultation:

```bash
cafe chat <role> --phase <step> --read-only -p "Read-only diagnosis: explain the current failure and propose one bounded next action. Do not edit files, artifacts, tasks, baton, blackboard, or workflow state, and do not run commands that change state."
```

The flag suppresses CAFE chat lifecycle writes and applies the configured
provider's native restrictions. It does not confine every provider-owned UI,
settings, integration, or persistence path. Keep diagnosis bounded; a provider
option or backend failure is an error, never a reason to retry writable chat.

| Priority and visible condition | One Manager action |
| --- | --- |
| 1. Worker, task, baton, continuation/session, or process state is stale, conflicting, or ambiguous | Diagnose runtime state first and fail closed. Do not chat or resume until the active work is clear. |
| 2. Legacy terminal-operation state reports `FAILED` or `LOST` | Preserve it and pause. Use only an applicable existing owner-specific recovery contract; never relaunch the old arbitrary command. |
| 3. A mandatory, `user_required`, permission, capability, strategy, scope, external-effect, model-chain, or other user-owned decision is pending, except a phase-agent recovery choice handled by priorities 6 and 8 | Present it to the user through its declared boundary. Do not ask the phase agent to infer or approve it. |
| 4. A scheduled proactive-review or confirmation boundary is due | Follow the confirmed review and handoff contracts. |
| 5. Visible behavior identifies a playbook, phase contract, Manager, or CAFE-core defect | Stop normal execution and follow `diagnosis_and_repair.md` for that layer. |
| 6. The same phase-agent failure keeps returning after its automatic retry budget, or retry safety cannot be established | Retain the pause. Consult the responsible phase agent once with the read-only diagnostic command above, then present every declared recovery option and practical consequence to the user. |
| 7. Visible phase progress exists but there is no valid handoff, or reported success conflicts with current state | Consult the responsible phase agent with the read-only diagnostic command above to identify completed work, the missing boundary, and one bounded next action. Do not reconstruct the handoff yourself. |
| 8. An eligible phase-agent interruption has a safe same-session retry and remaining budget | Apply the bounded automatic recovery procedure below without another user confirmation. |

## Bounded automatic recovery

For a stopped phase agent that exits unsuccessfully without a terminal completion
signal (including an unclassified Codex exit 1 with empty stderr), automatically
retry the same session at most three times, waiting 30 seconds before each retry.
The original execution is not a retry: the bound is one original execution plus
three retries. The delay reuses the first existing same-CLI retry delay in
`AgentManager.TRANSIENT_RETRY_DELAYS_SECONDS` (30 seconds); its separate
30/120-second transient-error backoff is not changed by this Manager policy.
An unknown provider root cause alone does not require a user decision when the
stopped execution, durable task and safe continuation are clear.

This is a narrow Manager recovery exception for the declared `retry` outcome of
`agent-execution-interrupted`, not a change to generic HumanTask ownership.
It applies in attached, unattended and event-driven Manager operation. Explicit
user instructions to stop or require manual recovery override the default.

1. Inspect current status, the active interruption task, sanitized error evidence,
   continuation/session and worker ownership. Require a terminated failed agent,
   no duplicate live execution, a resumable existing session and a declared
   `retry` continuation to the same step. Do not retry cancellation, a known
   configuration/authentication/permission error, invalid completion artifacts,
   an exhausted lower-level transient retry sequence, a confirmed runtime defect,
   or an external mutation whose outcome is unknown. Resolve those through the
   existing owner-specific path. Do not replay arbitrary unfinished commands.
2. Reconstruct the retry count from existing completed recovery tasks and failure
   history for the same workflow, step and iteration. Count prior same-session
   recovery retries, including user-selected retries and fresh-session recoveries;
   do not reset the count on
   another callback, Manager session, or differently worded error. Callback
   `attempt` and `hop` are transport metadata, not phase retry counts. Duplicate
   callbacks for a completed task do not consume another retry or launch work.
   Missing or conflicting history retains the pause for diagnosis. A successful
   phase boundary ends this budget; a later iteration starts a new budget.
   Do not use a string-similarity threshold or persist a new counter/store.
   Use the read-only bundled helper to reconstruct the budget from one stable
   view of existing task/results (no new counter or store):
   `python scripts/inspect_recovery_budget.py --issue-dir <issue-dir> --task-id <task-id>`.
   Inputs are the inspected issue directory and active interruption task ID;
   output is JSON with used/remaining retries, the 30-second delay and an advisory
   action. Pass `--stop-requested` after an explicit user stop. A nonzero exit or
   `retain_pause` requires diagnosis; `ignore_callback` launches nothing;
   `user_handoff` is exhausted. `inspect_retry_safety` supplies budget evidence
   only: worker/session/error/authority checks in step 1 remain mandatory. Re-run
   the helper after the delay; it neither sleeps nor completes/resumes any task.
3. When fewer than three retries have been used, report the next retry number and
   wait 30 seconds. If new input interrupts the wait, handle it first; it is not
   permission to skip the remaining delay. Reinspect the same pending task,
   stopped worker, session and authority after waiting. A changed task or user
   stop invalidates the pending automatic action.
4. Submit only the active task's `retry` result with `cafe task complete
   --no-resume --json`. Record the attempt number, 30-second delay and inspected
   error/task references in the existing result's `work_report`; this is recovery
   provenance, never a fabricated user answer. Verify durable completion, then
   rebuild fresh facts and resume through `run_workflow.py` in the confirmed mode.
   Preserve phase, iteration scope, CLI/model, session, permissions, capabilities
   and Delivery Contract. A wrapper `action: yield` still ends the Manager turn;
   subsequent failure callbacks continue from durable history.
5. After the third retry also fails, retain the pause, perform the bounded
   read-only diagnostic consultation once, and present the existing recovery
   choices to the user. Do not begin a fourth automatic retry, switch sessions,
   append a fallback, or expand authority. A user-selected retry after exhaustion
   authorizes that retry only; it does not silently reset the automatic budget.

## Recovery boundaries

- `agent-execution-interrupted` keeps its generic user-owned schema. The bounded
  automatic recovery exception above permits only the unchanged same-session
  `retry`; ineligible or exhausted recovery remains user-owned. Follow the
  durable completion and mode-specific resume sequence in `running_workflow.md`.
- Existing fresh-session recovery remains user-selected. Its availability does
  not authorize the Manager to choose it.
- Legacy terminal-operation `FAILED` or `LOST` state remains immutable. This
  policy creates no operation executor and never reruns the old command.
- A diagnostic `cafe chat` invocation must use `--read-only`, and its prompt
  must prohibit file and workflow-state changes.
  It cannot complete a HumanTask, grant authority, deliver input to another
  iteration, or prove completion. Apply its conclusion only through an existing
  legal task, input, correction, or authorization path; otherwise retain the
  pause.

Before diagnosing an interruption or requesting a retry, refresh `cafe
constraints list --issue <issue> --step <step> --json` and inspect the reported
constraint IDs. Re-resolve the effective CLI on each configured alternative.
Apply the shared registry's detection and mitigation guidance; do not substitute
remembered numeric limits. Follow the `constraint_assistance` handling contract
in `kickoff_execution.md` without granting new execution, cancellation or
HumanTask authority.
