# Driver Supervision And Recovery

Read this reference while workflow work is active or when execution pauses,
times out, becomes interrupted, or appears stale. Supervision policy belongs to
the Driver. Existing CAFE status, task, handoff, process, and output surfaces are
the evidence the Driver uses; do not require or create a failure fingerprint,
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

The Driver judges whether failures are the same in substance from their visible
meaning, failed boundary, and execution context. Exact wording need not match.
When the visible information is insufficient or conflicting, treat the state as
ambiguous and diagnose it instead of manufacturing certainty.

## Non-intervention envelope

The Driver remains passive while every applicable condition is demonstrably
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

Until `cafe chat` has an enforced read-only flag, use this diagnostic form:

```bash
cafe chat <role> --phase <step> -p "Read-only diagnosis: explain the current failure and propose one bounded next action. Do not edit files, artifacts, tasks, baton, blackboard, or workflow state, and do not run commands that change state."
```

| Priority and visible condition | One Driver action |
| --- | --- |
| 1. Worker, task, baton, continuation/session, or process state is stale, conflicting, or ambiguous | Diagnose runtime state first and fail closed. Do not chat or resume until the active work is clear. |
| 2. Legacy terminal-operation state reports `FAILED` or `LOST` | Preserve it and pause. Use only an applicable existing owner-specific recovery contract; never relaunch the old arbitrary command. |
| 3. A mandatory, `user_required`, permission, capability, strategy, scope, external-effect, model-chain, or other user-owned decision is pending, except a phase-agent recovery choice handled by priorities 6 and 8 | Present it to the user through its declared boundary. Do not ask the phase agent to infer or approve it. |
| 4. A scheduled proactive-review or confirmation boundary is due | Follow the confirmed review and handoff contracts. |
| 5. Visible behavior identifies a playbook, phase contract, Driver, or CAFE-core defect | Stop normal execution and follow `diagnosis_and_repair.md` for that layer. |
| 6. The same phase-agent failure keeps returning and no new observation gives a concrete reason another retry will differ | Keep the recovery task user-owned. Consult the responsible phase agent once with the read-only diagnostic prompt above, then inspect again before recommending another recovery action. |
| 7. Visible phase progress exists but there is no valid handoff, or reported success conflicts with current state | Consult the responsible phase agent with the read-only diagnostic prompt above to identify completed work, the missing boundary, and one bounded next action. Do not reconstruct the handoff yourself. |
| 8. A phase-agent failure has a safe idempotent retry and a concrete reason another attempt may differ | Present every declared recovery option and practical consequence, and recommend a retry under the unchanged contract. Do not submit the choice for the user. |

A materially different visible failure is a new incident. If the Driver cannot
tell whether it is materially different, classify it as ambiguous. Do not use a
string-similarity threshold, invent a count, or persist a new comparison record.
There is no fixed retry count for phase-agent execution recovery: recommend
another retry only while it remains safe and there is a concrete reason to
expect a different result. Stop repeating an unchanged failure without such a
reason.

## Recovery boundaries

- `agent-execution-interrupted` remains a user-owned recovery-choice HumanTask.
  The Driver may recommend an option but must present every declared option and
  relay only the user's explicit answer through the task flow in
  `running_workflow.md`.
- A user-authorized retry preserves phase, iteration scope, model chain,
  permissions, capabilities, and Delivery Contract. Recheck current visible
  state immediately before relaying the answer or resuming.
- Existing fresh-session recovery remains user-selected. Its availability does
  not authorize the Driver to choose it.
- Legacy terminal-operation `FAILED` or `LOST` state remains immutable. This
  policy creates no operation executor and never reruns the old command.
- A diagnostic `cafe chat` prompt must prohibit file and workflow-state changes.
  It cannot complete a HumanTask, grant authority, deliver input to another
  iteration, or prove completion. Apply its conclusion only through an existing
  legal task, input, correction, or authorization path; otherwise retain the
  pause.
