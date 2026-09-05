# Running And Inspecting A Workflow

Read this reference after kickoff and whenever starting, resuming, inspecting,
or retrying ordinary workflow work. Read `model_selection.md` before the first
execution and whenever agent work remains.

Before every start or resume, follow `project_global_skill_sync.md`: validate
the persisted runtime/catalog preflight against fresh read-only checks. Stop for
a fresh, separately scoped approval only when a comparison token changed.

## Operating modes

The kickoff records one mode; it is a skill operating contract, not a CAFE-core
policy.

- **attached** runs `cafe workflow --execute --mute-agent-output` in the
  foreground. Poll only at the confirmed positive interval. An empty terminal
  yield is transport state, not a reason to inspect early.
- **unattended** runs the continuous worker in the background. It has no
  proactive callback; inspect durable state when a user returns.
- **event-driven** runs that same continuous background worker, adding the
  trusted builtin callback below. It is not `--single-step`: phases continue
  normally whether the callback succeeds, fails, or never starts.

For event-driven mode, create the per-issue binding after `cafe prepare` and
before launch:

```bash
python3 <skill-dir>/scripts/workflow_event_callback.py --write-config \
  --issue-dir .cafe/issues/<issue> \
  --cli <claude|codex|gemini|copilot|cursor-agent> \
  --model <exact-model>

cafe workflow --issue <issue> --execute --mute-agent-output --background \
  --on-workflow-event builtin:use-cafe-workflow:workflow_event_callback
```

The callback uses `.cafe/issues/<issue>/driver/config.yaml`, `session.json`,
and `session.lock`. When the launch comes from the Codex App with `cli: codex`,
the binding records that visible Codex thread and the callback uses `codex queue`
to wake it through the existing host daemon. If the thread is busy, the notice
runs after its current turn; if it is idle, the notice wakes it. The callback
does **not** create a separate `__cafe_event_driver__` session. `session.json`
then records that same thread as the workflow's exact identity. A terminal or
non-Codex launch without a host-thread binding retains the existing per-issue
driver session behavior. Every path refuses to replace an acquired identity.
It is an ordinary driver and uses only existing kickoff authority:
confirmation contract, mandatory HumanTask stops, reactive user handoffs,
mandate, and model-adjustment authority.

The callback receives only an asynchronous durable-event notice. It must
re-check `cafe status`/`cafe show`; a notice can be stale. It may diagnose and
perform actions already authorized by the kickoff. It cannot wait for, collect,
infer, or choose an answer for a mandatory, `user_required`, clarification,
permission, or capability task, nor grant permissions or capabilities. It may
complete a declared `driver_confirmable` task only after verifying the current
confirmation contract and evidence. It does not own the background worker or
gain a safe stop channel. An existing reliable, authorized control may be used
only after verification; this feature creates no PID registry, cancellation API,
recovery protocol, or stop guarantee.

## Completing a HumanTask

The callback is not an interaction channel. A mandatory, `user_required`,
clarification, permission, or capability task requires a **user-facing driver
turn** to receive the user's explicit answer. A `driver_confirmable` task may
instead be completed by any driver, including an event-driven callback, after
it verifies the confirmed contract and evidence. Both cases use the same durable
task flow:

1. Inspect the exact pending task with `cafe task inspect <task-id>` and read
   its declared input schema. Never reuse a stale task ID.
2. For user-owned tasks, serialize only the user's supplied answer into that
   schema. The driver may add the task ID required by the schema, but must not
   infer a decision, approval, permission, or missing answer. For a
   `driver_confirmable` task, use only its declared response after the required
   contract and evidence verification.
3. Run `cafe task complete <task-id> --result '<json>' --no-resume --json`.
   Treat an uncertain command result as unconfirmed: inspect durable task and
   handoff state before retrying. If the task is already complete, do not submit
   another answer.
4. After durable completion, continue with the confirmed mode: attached starts
   the foreground continuous workflow; unattended starts the ordinary background
   worker; event-driven starts the background worker with its trusted callback.

`--no-resume` is an internal driver control that separates durable task
completion from mode-specific continuation. Direct `cafe task complete` users
retain its normal automatic foreground-resume behavior and need not perform
this two-step flow.

## Commands and handoffs

- Resolve the current phase from `cafe status` and the structured baton, then
  use `--start-step` only for initial entry or bounded diagnosis.
- Resume the persisted baton with `cafe workflow --execute --mute-agent-output`.
  `cafe make` is valid when direct workflow controls are not required.
- Use `--single-step` only for manual, bounded diagnosis. No ordinary operating
  mode uses it.
- A background invocation cannot carry `--single-step`, `--start-step`, or
  `--add-dir`. It may stage an exact `--user-input` before spawning the worker.
- For a HumanTask, read `handoffs_and_alignment.md`, resolve the active
  HumanTask and its input schema, including current `human_task_id`, then follow
  **Completing a HumanTask** above. Never turn an unknown or stale task into
  phase input.
  Plain text is valid only for a task that explicitly declares the `feedback`
  schema.

## Inspection

- `cafe status` shows phase timeline and current baton.
- `cafe show <step> output`, `questions`, and `checklist` show the latest
  durable phase evidence.
- Read `blackboard.json` only if the commands do not explain a handoff.

Attached polling starts after the full confirmed interval. The first proactive
inspection is due only after that full interval. Each proactive poll captures
and reports one current system timestamp. Completion, errors,
HumanTasks, and substantive command output may wake attached observation
immediately; a transport-only yield does not.
Continue a single deferred wait for the remaining interval instead of starting
a shorter polling loop; wait on the same deferred operation.
A terminal session id, empty output, or host-tool yield is transport state, not
substantive process output. It must not trigger a short `write_stdin` poll.
Substantive lifecycle output may still wake the driver immediately.

For unattended runs, tell the user that progress is durable but not proactively
observed. For event-driven runs, explain that boundary callbacks are best effort
and do not delay advancement; their role is timely diagnosis and authorized
handling of anomalies, not worker control.

## Proactive driver review

Before every start or resume, read and validate the confirmed
`.cafe/issues/<issue>/driver/proactive_review.yaml` contract against the active
playbook. If it is absent, invalid, or its phase coverage no longer matches the
active playbook, stop for a complete replacement proposal and user
reconfirmation; do not infer a review policy from an earlier conversation.

Only an executed required phase becomes due for proactive review, and only
after it has durable output and the current Driver has a valid observation
point. A not_required phase, a skipped phase, and an all-not_required contract
perform no proactive review. The current Driver performs the review directly;
it must not launch a separate reviewer or create a review artifact that itself
needs proactive review.

For every due phase, review the exact current durable artifact against accepted
upstream requirements, relevant repository evidence, and available correction
history. Complete every applicable pass by explicitly checking both missing
necessary scope and excessive or unnecessary scope, including out-of-scope
work, unnecessary abstraction, and extension work. These checks apply equally
to code and non-code `spec` or `plan` output. An incomplete, interrupted, or
ambiguous pass is not a no-blocking result.

Then consolidate every currently observable blocker and send it through the
responsible phase's existing correction route; do not edit generated phase
artifacts or invent a side channel. After any correction or other candidate
change, re-review the changed durable artifact, its correction delta, and every
affected original requirement, repeating both scope checks. Stop with a
self-contained user handoff when correction needs user-owned authority,
permission, capability, scope selection, or an answer. A no-blocking result is
quality evidence only: it does not replace `driver_confirmable` evidence,
mandatory HumanTasks, or user approval, and it does not replace built-in review
or final PR review.

On resume, a prior clean result may be reused only when existing artifacts and
handoffs prove that the exact current durable artifact completed a full
no-blocking pass. Missing, stale, incomplete, or ambiguous proof requires a
new full pass. This fail-closed rule stores no review status or correction
history.

Apply this same contract in attached, unattended, and event-driven modes.
Attached reviews at normal observation points, unattended reviews when the user
next returns to inspect durable state, and event-driven callbacks may begin a
review after a notification. The callback remains asynchronous, best-effort,
fail-open, and non-gating for workflow advancement.

Do not edit workflow artifacts, blackboard, or `next_step.txt` by hand except
when repairing confirmed broken workflow state. Do not bypass CAFE by directly
asking an agent to implement the issue.
