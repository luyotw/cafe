# Running And Inspecting A Workflow

Read this reference after kickoff and whenever starting, resuming, inspecting,
or retrying ordinary workflow work.

## Command checklist

- Start with the user's requirement:

  ```bash
  cafe make --user-input "<requirement or answer>. Strategic context: .cafe/strategic_context.yaml (issue: <issue-name>)"
  ```

- Resume normally:

  ```bash
  cafe make
  ```

- Retry a specific step only when a bounded retry is justified:

  ```bash
  cafe workflow --execute --start-step <step>
  ```

- Add `--single-step` for one-step diagnosis:

  ```bash
  cafe workflow --execute --start-step <step> --single-step
  ```

- Use `--fallback-preset <preset>` when the primary CLI is unavailable,
  rate-limited, missing, or configured with a bad model.
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

## Operating rules

- Prefer `cafe make`; legacy per-step commands were removed in issue #315.
- Do not edit workflow artifacts, blackboard, or `next_step.txt` by hand unless
  repairing confirmed broken state.
- Do not bypass CAFE by directly asking an agent to implement the issue.
- If CAFE reports uncommitted chat handoff changes, commit or stash the relevant
  changes before resuming.
- If CAFE reports a baton contract error, rerun the responsible step so the
  agent can rewrite the baton:

  ```bash
  cafe workflow --execute --start-step <step>
  ```

- If PR sync fails because the branch has uncommitted changes, commit or stash
  them, then rerun `cafe make`.
- If behavior appears incorrect rather than merely incomplete, stop retries and
  read `diagnosis_and_repair.md`.
