# Running And Inspecting A Workflow

Read this reference after kickoff and whenever starting, resuming, inspecting,
or retrying ordinary workflow work. Also read `model_selection.md` before the
first execution and whenever execution returns control with agent phases still
unexecuted.

## Command checklist

- Before every start or resume, follow `project_global_skill_sync.md`: validate
  the persisted runtime/catalog preflight against fresh read-only checks, stay
  silent for unchanged or non-actionable catalogs, and stop for a fresh,
  separately scoped approval when a comparison token changed.

- Read and validate the complete version 2 driver policy from the active
  issue's `issue.yaml`. Invalid, absent, legacy, or partial policy stops before
  workflow mutation and returns to `kickoff.md` for a fresh explicit choice.

## Bounded version 2 policy replacement

When an existing issue receives an explicit version 2 policy, collect a fresh
complete choice set instead of reading or translating legacy state. Invoke the
typed updater with every applicable value in one request:

```bash
cafe update-driver-policy <issue> --contract-version 2 \
  --driver-mode <attached|unattended|delegated> \
  [--poll-interval-seconds <seconds>] \
  [--delegated-cli <claude|codex|gemini|copilot|cursor-agent> \
   --delegated-model <exact-model>]
```

Use the attached option only with `--poll-interval-seconds`, and the delegated
options only with delegated mode. Missing, partial, legacy-derived, or
mode-inapplicable input must stop before mutation. The updater changes only the
active-worktree policy slice; it preserves unrelated issue metadata and all
blackboard state, and never writes policy into the repository-root inventory
pointer.

- Resolve the current phase from `cafe status` and the structured baton, then
  start it with the user's requirement:

  ```bash
  cafe workflow --execute --mute-agent-output --start-step <step> \
    --user-input "<requirement or answer>. Strategic context: .cafe/strategic_context.yaml (issue: <issue-name>)"
  ```

- Resume the phase declared by the persisted baton without new input:

  ```bash
  cafe workflow --execute --mute-agent-output
  ```

  For a prepared issue, `cafe make` is also a valid launcher when direct
  workflow controls are not needed:

  ```bash
  cafe make
  ```

- Answer an authorized user handoff without overriding its structured baton.
  First read `handoffs_and_alignment.md`, resolve the active HumanTask and its
  input schema, then use the documented JSON payload with the current
  `human_task_id`. Runtime resolves that durable ID to the task's recorded step
  and trigger before any intent-based or generic `--user-input` routing; an
  unknown, stale, or mismatched ID must stop instead of becoming phase input.
  If its declared outcome continues to an agent phase,
  reassess and configure that phase's model chain before submitting the payload,
  because the same invocation continues automatically through subsequent agent
  phases in `continuous` mode or executes the next single step in `single_step`
  mode. Plain text is valid only for a task that explicitly declares the
  `feedback` schema;
  never use it for `decision`, `answers`, or `target`.

- Add `--start-step <step>` only for the initial entry point or when a bounded
  retry/diagnosis must deliberately replace the current baton position. Never
  use it merely to resume a user-owned handoff.

- Keep each confirmed primary and any optional fallback chain in the active
  worktree's `.cafe/phases.yaml`. Install or change them with `write_phase_config.py`, then
  verify the affected step through the core parser before execution.
- Use repeated `--add-dir <path>` for existing extra directories. Prefer stable
  configuration in `.cafe/config.yaml` as `allowed_directories`.

## Progress inspection

For an outliving unattended or delegated run, report notification availability
before leaving the initiating conversation. If HumanTask delivery is
unavailable, say explicitly that the conversation will not be proactively
woken or updated and direct the user to `cafe status` and `cafe show` for
durable progress. If delivery is available, explain that only newly
materialized HumanTasks may notify. Permission needs, errors, and completion
remain durable inspection outcomes; phase boundaries, polling, empty yields,
and transport activity never notify.

- `cafe status`: phase timeline and current state.
- `cafe show <step> output`: latest phase result.
- `cafe show <step> questions`: current clarification request.
- `cafe show <step> checklist`: incomplete phase work.
- `cafe show <step> streaming --iteration <n>`: display the complete raw
  provider response for a specific iteration; omit `--iteration` for the latest
  saved response. This command reads the entire saved file, so offer it for
  direct human transcript viewing, not as driver diagnosis context. The durable
  file is
  `.cafe/issues/<issue>/<step>/iteration_NNN/streaming.jsonl` in the active
  issue worktree.
- `.cafe/issues/<issue>/blackboard.json`: use only when command output does not
  explain the handoff.

In attached mode, treat `driver.poll_interval_seconds` as the required cadence
for proactive calls to these inspection surfaces while the current process remains
active. Start the timer when the process starts or
resumes, before waiting for its first output. The first proactive inspection is
due only after that full interval; do not use a shorter startup or warm-up
cadence. If nothing wakes the driver first, perform one proactive inspection
when the interval elapses, then restart the timer.

A terminal session id, empty output, host-tool yield, deferred cell id, or a
generic "still running" result is transport state, not substantive process
output. It must not trigger a short `write_stdin` poll, `cafe status`, artifact
reads, a user-facing progress message, or any other sub-interval inspection.
Continue a single deferred wait for the remaining interval instead; one long
`write_stdin` wait is acceptable when that is how the host resumes the existing
session. If the host returns control before that wait finishes, wait on the same
deferred operation rather than starting a new terminal poll. Do not implement a
180-second contract as a loop of 30-second `write_stdin` calls.

At each proactive deadline, capture the current system time in the same
inspection operation and print it before the status result. Use an unambiguous
local timestamp such as `YYYY-MM-DD HH:MM:SS TZ`, and begin the corresponding
driver-to-user update with that exact captured time. For example:

```bash
date '+%Y-%m-%d %H:%M:%S %Z'
cafe status
```

Generate the timestamp from the system clock at poll time; do not estimate it
from conversation context. Substantive lifecycle output, command completion,
errors, HumanTasks, and other event-driven signals still wake the driver
immediately; if execution remains active after handling the signal, restart the
timer. A transport-only yield does not wake the driver. Stop polling when the
command exits, the workflow reaches a user-owned handoff or `done`, or execution
stops on an error. A host may require more frequent user-facing heartbeat
messages; those messages must not trigger an extra CAFE status or artifact poll,
and do not emit empty-progress chatter merely because the transport yielded.

Keep `--mute-agent-output` on direct `cafe workflow` driver executions so
provider narration is parsed and persisted without being copied into driver
context. `cafe make` does not expose that flag and remains a valid launcher.
The mute flag does not suppress workflow lifecycle events, errors,
HumanTasks, final artifacts, or `streaming.jsonl`. Direct a user who wants the
transcript to `cafe show <step> streaming --iteration <n>` or its durable file.
During bounded diagnosis, resolve the exact durable file and inspect only the
minimum relevant portion with
`rg -n -C <context-lines> '<pattern>' <streaming-file>` or
`tail -n <line-count> <streaming-file>`. Do not use
`cafe show <step> streaming` for driver diagnosis, and do not use the raw stream
as routine progress context.

When CAFE pauses for user input, read `handoffs_and_alignment.md` before
answering or resuming. Non-interactive resumption is allowed only when the exact
answer or permission already exists in the current thread, or the confirmed
contract delegates that specific decision to the driver.

Before execution, resolve and configure every phase chain. Do not interrupt a
healthy unattended or delegated run at phase boundaries. Attached mode returns
responsibility to the initiator after a boundary; polling remains read-only.
Manual `--single-step` is a diagnostic invocation control, not persisted
policy. If a user handoff sits before remaining agent work, reassess those
future chains under `model_selection.md` before submitting the structured
response. A terminal `_done` baton has no future chain to adjust.

## Operating rules

- Use `cafe make` for a prepared workflow when its environment preflight and
  simpler invocation are useful. Use
  `cafe workflow --execute --mute-agent-output` when direct controls are needed,
  and use `--single-step` only as an explicit manual or diagnostic control.
- `cafe workflow --execute --mute-agent-output --background` resumes the durable
  workflow through the fixed worker and returns its PID. It cannot carry
  `--single-step`, `--start-step`, or `--add-dir`.
- `--background --user-input '<exact payload>'` durably stages initial input,
  or validates and persists a current HumanTask response, before spawning the
  fixed worker. An invalid, stale, mismatched, or inapplicable HumanTask
  response must leave the workflow paused and must not start a worker. Prefer
  this single command when answering a HumanTask and continuing in the
  background:

  ```bash
  cafe workflow --issue <issue> --execute --mute-agent-output --background \
    --user-input '<exact structured JSON with human_task_id>'
  ```

  The input is durable before process ownership is transferred, so a worker
  startup failure can be retried without submitting the HumanTask again.
- A bounded diagnostic reproduction may temporarily add `--single-step` while
  continuous execution itself is under investigation. Record it as a diagnostic
  override; it does not mutate the confirmed execution contract.
- Do not edit workflow artifacts, blackboard, or `next_step.txt` by hand unless
  repairing confirmed broken state.
- Do not bypass CAFE by directly asking an agent to implement the issue.
- If CAFE reports uncommitted chat handoff changes, commit or stash the relevant
  changes before resuming.
- If CAFE reports a baton contract error, rerun the responsible step so the
  agent can rewrite the baton:

  ```bash
    cafe workflow --execute --mute-agent-output --start-step <step>
  ```

- If PR sync fails because the branch has uncommitted changes, commit or stash
  them, then rerun the PR phase using the confirmed execution mode.
- If behavior appears incorrect rather than merely incomplete, stop retries and
  read `diagnosis_and_repair.md`.
