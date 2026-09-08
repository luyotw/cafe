# Running And Inspecting A Workflow

Read this reference after kickoff and whenever starting, resuming, inspecting,
or retrying ordinary workflow work. Read `model_selection.md` before the first
execution and whenever agent work remains.

Before every start or resume, follow `project_global_skill_sync.md`: validate
the persisted runtime/catalog preflight against fresh read-only checks. A
changed comparison token triggers the reference's bounded semantic comparison,
not an automatic user stop. A catalog publication action exists only when the
user explicitly requests it; ordinary project-only entries and the optional
end-of-contract mismatch recommendation never stop start or resume. Use
`cafe catalog check --json` directly during ordinary start or resume checks;
the reminder script runs only while rendering a complete new or stale kickoff
contract. Reconfirm kickoff only for a material difference found by the semantic
comparison. Verified metadata-only churn may continue, while uncertain
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
primary CLI plus fallback CLI/model order only in memory. Waking the primary
session never includes a model override. `dispatch_state.json` is mutable runtime
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

When the first entry is Codex and activation runs from the Codex App, its
runtime-owned host thread is a best-effort hint for the first session. A
persisted acquired session always wins, and host-binding failure warns without
blocking workflow execution. A successfully bound host session uses
`codex queue`; no fallback inherits it. Otherwise the actual callback resumes
only that entry's persisted provider session or bootstraps an unbound entry.
Bootstrap never counts as event delivery or acceptance. Only actual callback durable acceptance stops
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
user handoffs, and mandate. It cannot change confirmed models.

The callback receives only an asynchronous durable-event notice. It must
re-check `cafe status`/`cafe show`; a notice can be stale. It may diagnose and
perform actions already authorized by the kickoff. It cannot wait for, collect,
infer, or choose a user answer for a mandatory, `user_required`, clarification,
permission, or capability task, nor grant permissions or capabilities. The
correction revise is not a user answer: only this declared correction outcome
is excepted from the callback prohibition, and only after due review/chat
consensus. It may complete a declared `driver_confirmable` task only after
verifying the current confirmation contract and evidence. It does not own the background worker or
gain a safe stop channel. An existing reliable, authorized control may be used
only after verification; this feature creates no PID registry, cancellation API,
recovery protocol, or stop guarantee.

## Completing a HumanTask

The callback is not an interaction channel. Except for an active declared
non-advancing correction revise, a mandatory, `user_required`, clarification,
permission, or capability task requires a **user-facing driver turn** to
receive the user's explicit answer. The exception for an active declared
non-advancing correction revise permits the current Driver, including an
event-driven callback, to submit only that revise after due review/chat
consensus; it never permits confirmation or another user-owned decision. A
`driver_confirmable` task may instead be completed by any Driver, including an
event-driven callback, after it verifies the confirmed contract and evidence.
Both cases use the same durable task flow:

1. Inspect the exact pending task with `cafe task inspect <task-id>` and read
   its declared input schema. Never reuse a stale task ID.
2. Classify the task before serializing its result. For an active declared
   non-advancing `revise` requiring feedback and marked `correction: true`, the
   Driver may serialize the correction result only after complete review and
   `cafe chat` consensus, including the consolidated findings, consensus, and
   acceptance conditions. For a user-owned task, serialize only the user's
   supplied answer into that schema; the Driver may add the task ID required by
   the schema, but must not infer a decision, approval, permission, or missing
   answer. For a `driver_confirmable` task, use only its declared response after
   the required contract and evidence verification.
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

Before every start or resume, validate the confirmed
`.cafe/issues/<issue>/driver/contract.json` and use its
`proactive_review.phase_decisions` projection. If the contract is absent,
invalid, stale, or its phase coverage no longer matches the active playbook,
stop and require kickoff reconfirmation; do not infer a review policy from an
earlier conversation. An explicit user-requested phase-only update does not
reopen unrelated kickoff policy.

Only an executed required phase becomes due for proactive review, and only when
its durable output has reached an existing scheduled confirmation pause that
blocks downstream agent work. Complete the review before completing a
`driver_confirmable` task or relaying a `user_required` answer that would resume
the workflow. A phase that advances immediately is not eligible for `required`;
its kickoff decision must be `not_required` because an asynchronous Driver
cannot review it in time to gate advancement. A not_required phase, a skipped
phase, and an all-not_required contract perform no proactive review. The
current Driver performs the review directly; it must not launch a separate
reviewer or create a review artifact that itself needs proactive review.

For every due phase, review the exact current durable artifact against accepted
upstream requirements, relevant repository evidence, and available correction
history. Complete every applicable pass by explicitly checking both missing
necessary scope and excessive or unnecessary scope, including out-of-scope
work, unnecessary abstraction, and extension work. These checks apply equally
to code and non-code phase output. An incomplete, interrupted, or ambiguous
pass is not a no-blocking result.

Bind that work to a composite review snapshot: artifact identity,
accepted-requirements identity, correction-history identity, active task
identity, handoff/baton identity, and driver-contract identity. Re-resolve and
compare the complete snapshot immediately before invoking chat and immediately
before task completion, confirmation, or reuse of a clean result. Any mismatch
invalidates the review/chat result: retain the pause and restart the full
review from the current snapshot. The snapshot also binds phase configuration
identity, resolved CLI/model identity, persisted session identity, playbook
chat-skills identity, and prepared chat-environment identity. An artifact-only
match is insufficient; resolve these inputs through the same existing chat
configuration path at both checks rather than inventing a second session or
environment mechanism.

The Driver must complete all applicable review passes before producing one
bounded findings batch. It names the reviewed phase and role, the exact current artifact
identity, every observable blocker, its requirement or boundary, and concise
evidence. Deliver that one batch through `cafe chat <role> -p` to the existing
responsible phase-agent session. Ask the agent to accept or rebut each finding.
Chat must not edit the current phase output: the prompt is discussion only,
and the chat response is discussion evidence, not workflow authority.

The bounded consumer accepts at most 20 findings and at most 12,000 UTF-8
bytes for the rendered prompt; each evidence item is limited to at most 500
UTF-8 bytes. The 120-second timeout and 4,000-byte output cap are policy-only
Driver limits: treat a breach as ambiguous and retain the pause. The generic
`cafe chat` runtime does not enforce them, so the Driver must not claim runtime
enforcement or fabricate a provider-side kill/receipt. Ordinary user-initiated
chat behavior remains unchanged. An over-budget batch remains paused and fails
closed. The Driver must not truncate, split, or silently omit findings or
evidence to fit a limit; retain the pause and obtain the applicable user-owned
scope decision before a new full review can form a compliant batch.

Findings, chat attempts, disagreements, and rebuttals do not create an
iteration. Independently verify a rebuttal against the same unchanged artifact.
An accepted finding without a durable correction remains blocking. If the
Driver and phase agent agree that an artifact correction is necessary, the
Driver may intentionally create one formal correction iteration only through
the active declared `revise` outcome. First verify that the decision requires
feedback, declares `correction: true`, and routes to correction rather than
downstream advancement. Submit `cafe task complete ... --no-resume --json` with
consolidated findings, reached consensus, and acceptance conditions; then verify
the durable task result and correction continuation before resuming in the
configured mode. Only the resumed runtime materializes and executes the next
formal iteration. Inspect its durable input, delta, and output only at the next
observable pause or failure, then complete Driver re-review of the resulting
artifact and every affected requirement.

After any correction or other candidate change, re-review the changed durable
artifact, its correction delta, and every affected original requirement,
repeating both scope checks. A partial, ambiguous, interrupted, failed, stale,
or unresolved attempt must fail closed: retain the pause, restart from the
current artifact identity, and complete a fresh full review. Stop with a
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

Apply this same contract in attached, unattended, and event-driven callback
modes, but only at the existing scheduled pause. Attached mode reviews before
its paused handoff resumes, unattended mode reviews when the user returns while
that pause is still pending, and an event-driven callback may begin after the
durable pause notification. A callback acting as the current Driver may submit
the same pre-authorized declared correction revise after due review/chat
consensus, but may not choose advancing confirmation or a user-owned decision.
A phase-terminal callback that did not pause cannot make the review gating and
must not be treated as a valid review opportunity. Callback failure must fail
closed at the existing pause; callbacks remain asynchronous, best-effort, and
non-gating for workflow advancement.

Do not edit workflow artifacts, blackboard, or `next_step.txt` by hand except
when repairing confirmed broken workflow state. Do not bypass CAFE by directly
asking an agent to implement the issue.
