# Running And Inspecting A Workflow

Read this reference after kickoff and whenever starting, resuming, inspecting,
or retrying ordinary workflow work. Read `model_selection.md` before the first
execution and whenever agent work remains.

Before every start or resume, follow `project_global_skill_sync.md`: validate
the persisted runtime/catalog preflight against fresh read-only checks. A
changed comparison token triggers the reference's bounded semantic comparison,
not an automatic user stop. Request the exact separately scoped approval only
for a fresh action, and reconfirm kickoff only for a material difference found
by that comparison. Verified metadata-only churn may continue, while uncertain
differences fail closed.

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

For event-driven mode, activate the confirmed Driver contract after
`cafe prepare`, validate the Driver-only entry, and then launch generic CAFE
through its existing event callback path:

```bash
python3 <skill-dir>/scripts/validate_driver_entry.py \
  --issue-name <issue> \
  --issue-dir .cafe/issues/<issue> \
  --workflow-id <prepared-workflow-id> \
  --fresh-facts '<fresh-driver-policy-facts-json>'

cafe workflow --issue <issue> --execute --mute-agent-output \
  --background \
  --on-workflow-event builtin:use-cafe-workflow:workflow_event_callback
```

The callback reads the issue-scoped `driver/contract.json` and projects the
event CLI/model order only in memory. `dispatch_state.json` is mutable runtime
state bound to that contract's digest: it contains sessions, attempt history,
the sticky active index, takeover, exhaustion, recovery, and timestamps, but
never a copy of mode, model-chain, or other confirmed policy. A changed digest
fails closed before dispatch. `driver/config.yaml` is a legacy migration input
only; when a contract exists it is neither read as callback authority nor a
writer target. The event-driver lifecycle uses no session-file discovery,
directory diff, sleep, polling, or watcher.

For attached or unattended Driver-managed work, invoke the same validator,
then start generic CAFE through its ordinary command. The supplied fresh facts are
the current bounded semantic policy rebuilt by the skill's loaders and the
current material assumptions; they are not a caller-selected subset. The
validator does not inspect `issue.yaml`, phase chains, or PR choices. Generic
CAFE validates and consumes those ordinary inputs under the existing #467
contract, with identical behavior whether a Driver exists or not.

Session acquisition and actual delivery are separate boundaries. Every
unacquired, unbound entry first runs a provider request exactly equivalent to
`say "HI"` with no workflow event or driver authority. Codex, Claude, Gemini,
Cursor, and Copilot each supply a provider-created session ID from their
verified structured or terminal evidence. The callback persists that ID in
`dispatch_state.json` before the actual callback. An existing acquired session
is reused without bootstrap. Copilot has the same lifecycle and never receives
a caller-selected new-session ID.

When the first entry is Codex and configuration runs from the Codex App, the
first Codex entry's valid runtime-owned host binding is already acquired and
uses `codex queue`; no fallback inherits it. Otherwise the actual callback
resumes only that entry's persisted provider session. Bootstrap never counts as
event delivery or acceptance. Only actual callback durable acceptance stops
forward routing, makes that entry active for later events, and records a
takeover. The provider acknowledgement is bound to the exact event identity in
the dispatched invocation before it can satisfy acceptance. This is transport
acceptance and does not wait for or infer success from model output.

Entries are attempted serially from the sticky active index. Only a conclusive
pre-acceptance nonacceptance may move to the next later entry. An ambiguous
outcome stops forward routing and remains recovery-visible. Exhaustion retains
the event and all attempts for existing explicit recovery; it does not roll
back completed phase work or block normal phase advancement. A cross-provider
takeover is transport-local and does not merge conversations or promise that
the initiating conversation continues elsewhere.

Inspect this state without acquiring a callback lock or modifying any driver
file:

```bash
python3 <skill-dir>/scripts/workflow_event_callback.py \
  --status --issue-dir .cafe/issues/<issue>
```

The projection reports confirmed order/conformance, acquisition separately
from delivery, the active transport, takeover, exhaustion, and recovery. It
does not infer delivery from model output or claim cross-provider context
continuity. The callback remains an ordinary driver and uses only existing
kickoff authority: confirmation contract, mandatory HumanTask stops, reactive
user handoffs, mandate, and model-adjustment authority.

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

Do not edit workflow artifacts, blackboard, or `next_step.txt` by hand except
when repairing confirmed broken workflow state. Do not bypass CAFE by directly
asking an agent to implement the issue.
