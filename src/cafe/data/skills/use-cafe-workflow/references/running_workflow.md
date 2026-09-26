# Running And Inspecting A Workflow

Read this reference after kickoff and whenever starting, resuming, inspecting,
or retrying ordinary workflow work. Read `model_selection.md` before the first
execution and whenever agent work remains.

For active supervision, interruption classification, or recovery, read
`supervision_and_recovery.md`. Its non-intervention envelope decides whether
the Driver remains passive before the ordinary commands below are considered.

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

The Driver must never execute `release-check` while a workflow is active. Defer
an in-workflow request until the workflow is complete; the user may run it
before release.

Whenever an existing supervision rule already requires a user-facing reply,
append the localized renderer output required by `workflow_progress.md`.
Rendering that reply must not add a status poll, resume, task completion, or other
intervention.

For Driver-managed preparation, resolve the user-facing runtime-update decision
from `project_global_skill_sync.md` before invoking `cafe prepare
--no-interactive`; callbacks never supply this answer.

## Required Driver launch entrypoint

The kickoff records one mode; it is a skill operating contract, not a CAFE-core
policy.

Every Driver-managed start and ordinary resume must use the same wrapper:

```bash
python3 <skill-dir>/scripts/run_workflow.py \
  --issue <issue> \
  --playbook <confirmed-playbook> \
  --driver-mode <attached|unattended|event-driven> \
  --fresh-facts '<rebuilt-current-driver-facts-json>'
```

`--driver-mode` is an assertion against the confirmed contract. The wrapper
fails closed for missing, unreadable, stale, or conflicting workflow identity,
playbook, mode, callback binding, CLI order, or checkout identity. It reads the
prepared workflow and persisted baton for both start and resume; never add
`--start-step` for an ordinary resume.

Rebuild `--fresh-facts` from the current bounded runtime and catalog checks as
an object containing `semantic_facts.effective_policy`, rebuilt from the current
complete Driver policy. Runtime/catalog diagnostics stay outside the contract;
do not add `material_assumptions` or copy stale policy merely to make it match.
The wrapper validates that
payload through `evaluate_driver_entry` and rejects `material_change` and
`unknown` before launching any attached, unattended, or event-driven worker.
Continue only after the existing contract reconfirmation path establishes
`same_semantics`.

- **attached** launches foreground continuous execution and returns an
  `action: wait` directive containing the confirmed positive
  `poll_interval_seconds`. Poll only at the confirmed positive interval. An
  empty terminal yield is transport state, not a reason to inspect early.
- **unattended** launches background continuous execution without a callback
  and returns `action: yield`. That directive is terminal for the current
  Driver turn; inspect durable state only when the user returns.
- **event-driven** validates the trusted builtin callback and confirmed ordered
  Driver CLI chain, launches background continuous execution with the callback,
  and returns `action: yield`. That directive is terminal for the current
  Driver turn: do not poll with sleep, `ps`, `write_stdin`, `cafe status`, or
  `cafe task ls`; wait for a callback wake or user input.

None of the modes uses `--single-step`. The wrapper always supplies `--execute`
and `--mute-agent-output`, supplies `--background` only for unattended and
event-driven operation, and supplies the callback only for event-driven mode.
It never infers HumanTask answers, permissions, `--open-pr`, model or playbook
changes, `--add-dir`, `--no-verify`, or retry/fresh-session choices.
The only separately authorized continuation input is `--alignment-input`: it
requires explicit JSON and is accepted only when the current durable handoff,
confirmed Driver policy, and latest alignment request all authorize that exact
decision. It is not a HumanTask answer or a general phase-input channel.

The wrapper also emits stable `CAFE_DRIVER_DIRECTIVE` records for launch
failure and a durable user-owned boundary. Do not launch through a user-owned
boundary or infer its answer.

The callback reads the issue-scoped `driver/contract.json` and projects the
primary CLI plus fallback CLI/model order only in memory. Waking the primary
session never includes a model override. `dispatch_state.json` is mutable runtime
state that records sessions with their CLI/model identities, each event's
routing chain, attempt history, the sticky active index, takeover, exhaustion,
recovery, and timestamps. These recorded identities describe past dispatch;
the current contract controls new dispatch. The stored digest does not block
dispatch after a confirmed contract update.
`driver/config.yaml` is a legacy migration input only; when a contract exists
it is neither read as callback authority nor a
writer target. The event-driver lifecycle uses no session-file discovery,
directory diff, sleep, polling, or watcher.

The wrapper validates only launch authority and invocation identity. It does
not become a workflow state inspector or decision engine. After a callback wake
notice, continue to inspect `cafe status`, `cafe show`, and durable tasks before
deciding what the Driver may do.

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

Historical callback attempts retain their recorded route and session IDs. After
a confirmed transport change, matching CLI/model provider sessions move to
their new positions; other provider sessions are acquired again as needed. A
Codex host session remains usable only for a Codex primary. The active route
resets to the current primary, while prior events and attempts stay intact.
An event already attempted under an unknown or different route is not resumed
by interpreting its old indexes against the new chain; new events use the
current chain. No manual rebind is needed for a new callback.

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

## Project confirmed user context into agent input

Before any agent step, derive its inputs from the effective graph and project
one bounded delta only for facts that are user-confirmed in the current turn,
a completed HumanTask, or the current Driver contract; belong to this workflow;
remain current and relevant; and are not already visible through declared
artifacts, iteration input, or durable task results. This applies to every
playbook. Preserve exact wording when paraphrase could alter meaning.

Use a routing HumanTask only within its declared schema **and semantic purpose**;
correction feedback includes missing confirmed direction with the findings and
acceptance conditions. Otherwise use workflow `--user-input` only when the
current command and target step support it. Never replace a task-required
answer, use `--start-step` just to carry context, or edit artifacts, blackboard,
or baton state. If no legal input path exists, retain the pause and report it.

`cafe chat` is discussion evidence, not delivery to another iteration or step.
Supplemental context cannot replace required artifacts, ownership, review, or
authorization. Exclude inferred, superseded, unrelated, secret, credential, or
cross-task content; handle existing permission/capability grants only through
their exact declared boundary. Mandatory and `user_required` answers still need
the explicit user-facing relay, and callbacks may use only already durable facts.

The callback receives only an asynchronous durable-event notice. It must
re-check `cafe status`/`cafe show`; a notice can be stale. It may diagnose and
perform actions already authorized by the kickoff. It cannot wait for, collect,
infer, or choose a user answer for a mandatory, `user_required`, permission, or
capability task, nor grant permissions or capabilities. A `need_clarification`
task whose confirmed reactive policy is `driver_confirmable` may be answered
only when the complete answer stays within the confirmed Delivery Contract,
its scope, explicit constraints and existing authority, and triggers no deviation.
Contract changes, new permission or external-effect authority, mandatory
gates, reserved product or strategy decisions, and uncertainty about whether
authority already exists remain user-owned. Authorized reversible technical
choices may use repository precedent, smaller footprint, and reversibility as
tie-breakers; normal engineering uncertainty is not itself a user handoff. A
unique active declared correction outcome is not a user answer only when it
requires feedback, is marked `correction: true`, and routes to a
non-advancing correction continuation. Derive it solely from the active
HumanTask declaration regardless of outcome, phase, or target names; zero or
multiple eligible outcomes fail closed for user/playbook clarification. The
exception applies only after complete review and one `cafe chat` consensus
exchange. It may complete a declared `driver_confirmable` task only after
verifying the current confirmation contract and evidence. It does not own the background worker or
gain a safe stop channel. An existing reliable, authorized control may be used
only after verification; this feature creates no PID registry, cancellation API,
recovery protocol, or stop guarantee.

## Completing a HumanTask

The callback is not an interaction channel. A mandatory, `user_required`,
permission, or capability task requires a **user-facing driver turn** to
receive the user's explicit answer. The unique active declared correction
outcome exception permits the current Driver, including an event-driven
callback, to submit only that eligible outcome after complete review and one
`cafe chat` consensus exchange; it never permits confirmation or another
user-owned decision. A `need_clarification` task whose confirmed reactive
policy is `driver_confirmable` may be completed by any Driver, including an
event-driven callback, only within the confirmed Delivery Contract and existing
authority. Any other `driver_confirmable` task may likewise be completed after
the Driver verifies its confirmed contract and task-specific evidence. These
Driver-owned cases use the same durable task flow:

On every later user-facing turn, inspect current durable state first. If a
user-owned HumanTask is still pending and no adequate handoff has been given in
the current conversation, answer the user's immediate question briefly and
append the compact summary required by `handoffs_and_alignment.md`. Do not
repeat it when the user already has the same task and options unless they ask.

1. Inspect the exact pending task with `cafe task inspect <task-id>` and read
   its declared input schema. Never reuse a stale task ID.
2. Classify the task before serializing its result. The Driver may serialize a
   correction result only for the unique active declared correction outcome that
   requires feedback, is marked `correction: true`, and routes to a
   non-advancing correction continuation, and only after complete review and one
   `cafe chat` consensus exchange, including the consolidated findings,
   consensus, and acceptance conditions. If zero or multiple outcomes qualify,
   fail closed for user/playbook clarification. For a user-owned task, serialize only the user's
   supplied answer into that schema; the Driver may add the task ID required by
   the schema, but must not infer a decision, approval, permission, or missing
   answer. For a `driver_confirmable` task, use only its declared response after
   the required contract and evidence verification. For `need_clarification`,
   record a concise contract basis and do not submit when the answer changes
   the contract, triggers a deviation, needs new authority, is reserved to the
   user, or its authority is uncertain.
3. Run `cafe task complete <task-id> --result '<json>' --no-resume --json` only
   after the Driver-owned rules above authorize relaying or completing the
   response. The generic task command does not interpret or grant Driver
   authority. The response may include `work_report` with non-empty `summary`
   and `outcome`, plus optional `evidence` references, when recording work
   already performed. The report is metadata only: it cannot replace the task's
   required response or select a continuation.
   Treat an uncertain command result as unconfirmed: inspect durable task and
   handoff state before retrying. Repeating the exact normalized response is
   safe and does not resume twice; a different response conflicts.
4. After durable completion, rebuild fresh facts and invoke
   `scripts/run_workflow.py` with the confirmed issue, playbook, Driver mode,
   and `--fresh-facts`. The wrapper follows the persisted continuation.

`--no-resume` is an internal driver control that separates durable task
completion from mode-specific continuation. Direct `cafe task complete` users
retain its normal automatic foreground-resume behavior and need not perform
this two-step flow.

## Commands and handoffs

- Resolve the current phase from `cafe status` and the structured baton, then
  use the wrapper for every Driver-managed start or resume.
- Ordinary resume follows the persisted baton and completed durable task. Do
  not select or reconstruct a step with `--start-step`.
- Use `--single-step` only for manual, bounded diagnosis. No ordinary operating
  mode uses it.
- The wrapper has no input that grants user-owned decisions or separately
  authorized options. Complete those through their existing explicit boundary
  before invoking the wrapper.
- For a HumanTask, read `handoffs_and_alignment.md`, resolve the active
  HumanTask and its input schema, including current `human_task_id`, then follow
  **Completing a HumanTask** above. Never turn an unknown or stale task into
  phase input.
  Plain text is valid only for a task that explicitly declares the `feedback`
  schema.

Direct `cafe workflow` invocation is an explicit manual bypass only, never the
normal Driver path. Use it only when the user explicitly requests that bypass;
the caller then owns every argument and the wrapper's validation/directive
contract does not apply.

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
evidence. Deliver that one batch through
`cafe chat <role> --phase <step> -p "<bounded findings batch>"` to the existing
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

A host-tool yield or empty output with a live execution handle is not a
completed `cafe chat`. Continue waiting on the same process for up to 120
seconds cumulatively; never launch a duplicate chat or infer a missing response.
Only a completed process with a usable agent response satisfies the exchange.
If the process ends without one, the handle is lost, or the limit expires,
retain the pause and classify the result as ambiguous. Verify termination before
the single safe retry of this chat exchange.

Findings, chat attempts, disagreements, and rebuttals do not create an
iteration. Independently verify a rebuttal against the same unchanged artifact.
An accepted finding without a durable correction remains blocking. If the
Driver and phase agent agree that an artifact correction is necessary, the
Driver may intentionally create one formal correction iteration only through
the unique active declared correction outcome. First verify that it requires
feedback, declares `correction: true`, and routes to a non-advancing correction
continuation. If zero or multiple outcomes qualify, fail closed for
user/playbook clarification. Submit `cafe task complete ... --no-resume --json` with
consolidated findings, reached consensus, and acceptance conditions, plus any
relevant current user-confirmed direction missing from the target's declared
inputs; then verify
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
or any other graph-declared review.

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
the same unique eligible correction outcome after complete review and one
`cafe chat` consensus exchange. It may also complete a confirmed
`driver_confirmable` clean advancement after the required review and evidence
verification, but may not choose an advancing mandatory or `user_required`
confirmation or any other user-owned decision.
A phase-terminal callback that did not pause cannot make the review gating and
must not be treated as a valid review opportunity. Callback failure must fail
closed at the existing pause; callbacks remain asynchronous, best-effort, and
non-gating for workflow advancement.

Do not edit workflow artifacts, blackboard, or `next_step.txt` by hand except
when repairing confirmed broken workflow state. Do not bypass CAFE by directly
implementing the issue, except through the explicit, user-approved bounded
closeout route in `completion_and_authority.md`.

## Delivery evidence during execution and takeover

Before Driver-owned work, the entry adapter returns the confirmed
`delivery_contract` with its contract digest. Rebuild fresh facts from confirmed
user decisions and current bounded evidence; never echo persisted facts merely
to force a freshness match. Reuse the same product contract across providers.
A missing, malformed, stale or digest-mismatched contract stops Driver-owned
work for the existing reconfirmation handoff. An older full contract must be
explicitly reconfirmed before becoming a compact contract; do not drop its
restrictions or turn its advisory inputs into new authority during conversion.

At each existing eligible output confirmation, use the Delivery comparison in
`handoffs_and_alignment.md`. Derive the step and artifact names from the loaded
playbook and active task. Preserve authoritative phase outputs and declared
input edges; omitted `input_artifacts` means the existing full-source fallback,
whereas an explicit empty list means isolated inputs. Read complete sources
when excerpts cannot establish coverage. Do not insert a confirmation gate
where none exists or require a specification/planning phase.

A clean comparison only permits a confirmed `driver_confirmable` output.
Mandatory/user-required confirmations and reactive decisions retain their
owners in attached, unattended and event-driven modes. Callbacks remain
asynchronous and non-gating; they cannot collect or infer a user's answer.
Delivery comparison supplements confirmed proactive review and graph-declared
reviews; it adds no final review or completion gate.
