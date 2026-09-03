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
and `session.lock`. They keep the exact CLI/model/session isolated per issue.
It resumes the existing session when available and refuses to replace its
identity. It is an ordinary driver and uses only existing kickoff authority:
confirmation contract, mandatory HumanTask stops, reactive user handoffs,
mandate, and model-adjustment authority.

The callback receives only an asynchronous durable-boundary notice. It must
re-check `cafe status`/`cafe show`; a notice can be stale. It may diagnose and
perform actions already authorized by the kickoff, but it cannot answer a
mandatory HumanTask or grant permissions/capabilities. It does not own the
background worker or gain a safe stop channel. An existing reliable,
authorized control may be used only after verification; this feature creates
no PID registry, cancellation API, recovery protocol, or stop guarantee.

When completing or cancelling a HumanTask or capability task for either
background mode, do not use the task command's automatic resume. Use
`cafe task complete ... --no-resume` or `cafe task cancel ... --no-resume`,
then restart the continuous worker. Unattended uses `--background`; event-driven
uses the same `--background --on-workflow-event
builtin:use-cafe-workflow:workflow_event_callback` command. Attached may use
the task command's automatic foreground resume.

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
  HumanTask and its input schema, submit its current `human_task_id`
  payload, and never turn an unknown or stale task into phase input.
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
