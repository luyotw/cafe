# Running And Inspecting A Workflow

Read this reference after kickoff and whenever starting, resuming, inspecting,
or retrying ordinary workflow work. Also read `model_selection.md` before the
first execution and after every completed phase.

## Command checklist

- Before any start or resume invocation, follow
  `project_global_skill_sync.md`: run the read-only project/global skill check,
  stay silent when it reports `identical` or `no_project_skills`, and ask the
  user before applying any reported project-to-global update.

- Resolve the current phase from `cafe status` and the structured baton, then
  start it with the user's requirement:

  ```bash
  cafe workflow --execute --start-step <step> --single-step \
    --user-input "<requirement or answer>. Strategic context: .cafe/strategic_context.yaml (issue: <issue-name>)"
  ```

- Resume the phase declared by the persisted baton without new input:

  ```bash
  cafe workflow --execute --single-step
  ```

- Answer an authorized user handoff without overriding its structured baton.
  First read `handoffs_and_alignment.md`, resolve the active HumanTask and its
  input schema, then use the documented JSON payload with the current
  `human_task_id`. If its declared outcome continues to an agent phase,
  reassess and configure that phase's model chain before submitting the payload,
  because the same one-step invocation may execute the continuation. Plain text
  is valid only for a task that explicitly declares the `feedback` schema;
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

- `cafe status`: phase timeline and current state.
- `cafe show <step> output`: latest phase result.
- `cafe show <step> questions`: current clarification request.
- `cafe show <step> checklist`: incomplete phase work.
- `.cafe/issues/<issue>/blackboard.json`: use only when command output does not
  explain the handoff.

When CAFE pauses for user input, read `handoffs_and_alignment.md` before
answering or resuming. Non-interactive resumption is allowed only when the exact
answer or permission already exists in the current thread, or the confirmed
contract delegates that specific decision to the driver.

After every invocation that completes a phase, inspect the phase result and
actual CLI/model, duration, verification, and structured baton. Resolve the
actual skill for the next agent iteration, decide whether to keep or change its
chain under `model_selection.md`, and update `.cafe/phases.yaml` before that
phase executes. If a user handoff sits between phases, make this decision before
submitting the structured response that selects the continuation. A terminal
`_done` baton has no future chain to adjust.

## Operating rules

- Do not use `cafe make` for driver execution. It can cross multiple phase
  boundaries before the driver can reassess models. Use the generic
  `cafe workflow` command with `--single-step` and normally follow persisted
  workflow state.
- Do not edit workflow artifacts, blackboard, or `next_step.txt` by hand unless
  repairing confirmed broken state.
- Do not bypass CAFE by directly asking an agent to implement the issue.
- If CAFE reports uncommitted chat handoff changes, commit or stash the relevant
  changes before resuming.
- If CAFE reports a baton contract error, rerun the responsible step so the
  agent can rewrite the baton:

  ```bash
    cafe workflow --execute --start-step <step> --single-step
  ```

- If PR sync fails because the branch has uncommitted changes, commit or stash
  them, then rerun the PR phase in one-step mode.
- If behavior appears incorrect rather than merely incomplete, stop retries and
  read `diagnosis_and_repair.md`.
