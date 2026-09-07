# User Handoffs And Driver-Owned Alignment

Read this reference whenever CAFE pauses for a user, the driver considers
confirming an output, or a proposed delta may affect strategic alignment. Also
read `strategic_context.md`.

Command examples below allow the runtime to follow the persisted baton. Append
`--single-step` only as an explicit manual or diagnostic invocation control;
it is not part of the issue's driver policy.

The kickoff contract says who may approve an output or answer a reactive pause.
The mandate says what the driver may decide. These are independent controls and
CAFE runtime does not auto-approve them.

## User-pause checklist

When output contains `to_owner=user`, `confirm_output`, `need_clarification`,
`need_permission`, `alignment_checkpoint`, or `Workflow is waiting for user
input`:

- [ ] Identify `from_step`, `to_owner`, and `intent` from blackboard handoff,
  `next_step.txt`, or terminal output.
- [ ] For a HumanTask-backed handoff, resolve the active task ID, declared
  `task`, input schema, and current question IDs from the terminal output,
  `cafe show <from_step> questions`, and the matching pending record in
  `.cafe/issues/<issue>/human_tasks.json`. Do not guess or reuse an old task ID.
- [ ] Re-resolve the conversation locale.
- [ ] Read `playbook_id`, `confirmation_contract`, and
  `reactive_user_handoffs` from the active `issue.yaml`.
- [ ] Verify the exact confirmation-gate partition with
  `cafe playbook confirmation-gates <playbook-id>`.
- [ ] If the contract or locale is missing, stale, invalid, or omits an
  assignable pause policy, stop for the user and repair the contract before
  continuing. Mandatory HumanTask gates are not members of that partition.

Then route by intent:

- `confirm_output` from a mandatory HumanTask step: always stop for the real user.
- `confirm_output` from a `user_required` step: stop for user approval or
  correction.
- `confirm_output` from a `driver_confirmable` step: verify the output and
  required input artifacts are complete, in-mandate, and consistent with
  accepted upstream artifacts before confirming.
- `need_clarification`: stop unless the exact answer already exists in the
  current thread. Strategic documents are not a substitute for the answer.
- `need_permission`: stop unless the exact permission already exists in the
  current thread. Never grant production access, destructive actions, or
  external side effects for the user.
- legacy or custom `alignment_checkpoint`: use the classification below; the
  checkpoint is evidence, not proof the user must decide.
- any other user-owned pause: stop. Unknown handoffs are not driver-confirmable.

## Route proactive-review findings through existing handoffs

At an existing scheduled confirmation pause after a required phase, finish the
current-Driver review before completing a `driver_confirmable` task or relaying
a `user_required` answer that would resume the workflow. When the review finds
blockers, consolidate every currently observable finding. State both missing
necessary scope and excessive or unnecessary scope when applicable, then return
the batch through the responsible phase's existing correction path. Do not edit
the phase artifact, manufacture a replacement output, or turn the review result
into a new recursive review target.

If the existing path requires a clarification, permission, capability, scope
choice, strategic decision, or other user-owned authority, stop and present the
self-contained user decision above. A no-blocking driver review is quality
evidence only, never user confirmation and never a substitute for
`driver_confirmable` evidence. Re-review changed durable output through the
same process; keep built-in review and final PR review obligations separate.

## Present a self-contained user decision

Before asking the user to answer a HumanTask, clarification, permission, or
confirmation, prepare the response from the current task schema and phase
evidence. Assume the user has no terminal, repository checkout, or artifact
viewer.

- State the workflow phase, what completed, why it paused, and what will happen
  after the answer.
- Render every current question in the conversation, including its human-readable
  title, identifier when one exists, whether it is single-select, multi-select,
  free text, or a confirmation, and every available option.
- Number options and give a simple reply format with an example. For a
  multi-select question, explicitly say that the user may choose all options
  (for example, reply `all`) when that is valid; do not choose on the user's
  behalf.
- For every option, state its downstream effect and every option-specific
  required field. Render `requires_feedback`, `requires_target`, and
  `correction` requirements when present; list every `allowed_targets` value
  and show a valid reply example that includes the required feedback or target.
- Explain technical labels in plain language when needed to make the choice
  meaningful. Keep the request focused on the active task rather than dumping
  unrelated workflow files or raw runtime JSON.
- Never ask the user to open `questions.xml`, run a command, or inspect an
  artifact to discover the choices. You may link the generated artifact only as
  optional supporting material after reproducing the decision-relevant content
  in the message.

If an artifact is unusually long, summarize its relevant effects and still
render every decision option. Ask a follow-up only when the task schema itself
requires information not available in the current handoff.

Driver-confirmable means the driver verifies and resumes; it does not let a
phase agent approve itself. If the declared outcome continues to an agent
phase, first reassess and configure that phase's model chain, because the
driver will execute the continuation. Then submit the exact HumanTask response,
for example:

```bash
cafe task complete <active-human-task-id> \
  --result '{"task":"output-review","decision":"confirm","human_task_id":"<active-human-task-id>"}' \
  --no-resume --json
```

Use the active task's declared decision ID. Verify the durable result, then
continue using the confirmed mode from `running_workflow.md`. For a bounded
revision, include its required `feedback` instead of sending plain text. Stop for the user when
approval would change requirements beyond authority, public positioning,
business/legal/pricing decisions, production access, destructive operations,
or an ambiguous strategic tradeoff.

## Driver-owned alignment

Bundled playbooks omit `alignment:` configuration, so the globally registered
compatibility hook is inactive. The driver makes the semantic decision:

> Does the newest proposal remain within confirmed strategic documents and the
> user's mandate?

Alignment is not normal spec confirmation, implementation clarification, or
permission for an external side effect.

### Evaluation boundaries

Evaluate alignment:

1. during kickoff after reading strategic context;
2. before driver-confirming spec or plan;
3. when a correction changes requirements, product scope, positioning,
   principles, mandate, or trusted capability boundaries.

Do not re-evaluate unchanged scope merely because the workflow moved to
develop, review, or PR. Implementation-only corrections inherit the latest
accepted alignment result.

### Evidence tuple

Use the newest proposal delta, latest accepted spec, and only the relevant
strategic grounds. Ignore incidental keywords, negative-space statements,
generated boilerplate, and irrelevant artifact history. Record:

- `proposal_delta`: concrete changed scope;
- `strategic_ground`: exact document section, mandate axis, or out-of-mandate
  item;
- `mandate_level`: `agent`, `propose`, or `escalate`;
- `relation`: `within`, `contradicts`, `extends`, `missing_ground`, or
  `uncertain`.

Act on the tuple:

- `within` + `agent`: continue without asking.
- `within` + `propose`: state the grounded recommendation and continue only as
  the playbook allows.
- `within` + `escalate`: stop; the mandate reserves this decision.
- `contradicts` or `extends`: stop and ask one focused alignment question.
- `missing_ground` or `uncertain`: stop; do not invent strategy or silently
  narrow scope.

Except for an explicit `escalate` mandate, only a concrete proposal delta plus
a strategic ground may cause an alignment stop. A score assembled from weak
signals is insufficient.

Keep adjacent concerns separate:

- missing product or implementation facts use `need_clarification`;
- credentials, production access, destructive operations, and external side
  effects use `need_permission`;
- a clear in-roadmap implementation choice needs neither.

### Asking and resuming

Ask one focused question naming the governing axis, proposal delta, recommended
option, and tradeoff. When the user answers, submit the result to the exact
pending HumanTask, verify it, then continue using the confirmed execution mode:

```bash
cafe task complete <active-human-task-id> \
  --result '{"task":"clarification-answers","answers":{"<question-id>":"<answer>"},"human_task_id":"<active-human-task-id>"}' \
  --no-resume --json
```

Use every current question ID required by the active task. If its declared
input schema is `feedback` rather than `answers`, use:

```bash
cafe task complete <active-human-task-id> \
  --result '{"task":"clarification-feedback","feedback":"<answer>","human_task_id":"<active-human-task-id>"}' \
  --no-resume --json
```

Never convert an `answers`, `decision`, or `target` task into a plain-text
payload; runtime accepts plain text only for a declared `feedback` schema.

Update a strategic document only when the user explicitly confirms the new
strategic content. A driver-authored draft remains `draft` or `missing` unless
it is a mechanical copy of already confirmed material.

Driver takeover does not transfer conversation or provider-session authority.
The replacement Driver reads the same validated issue contract, refreshes
skill-owned evidence, and preserves every user confirmation, HumanTask,
permission, mandate, model-adjustment, proactive-review, and generic PR
publication boundary. If that proof is material, ambiguous, stale, malformed,
or belongs to another workflow, stop for the documented reconfirmation path.

### Legacy or custom core checkpoints

For an explicit `alignment_checkpoint`:

1. Read the latest
   `.cafe/issues/<issue>/<step>/iteration_*/alignment_request.json`.
2. Apply the same evidence tuple.
3. For `within` + `agent`, resume with explicit JSON; plain text must not
   approve the checkpoint:

   ```bash
   cafe workflow --execute --mute-agent-output \
     --user-input '{"decision":"approve","reason":"Within confirmed roadmap and mandate."}'
   ```

4. For `within` + `propose`, use the playbook's grounded recommendation flow.
5. For `escalate`, `contradicts`, `extends`, `missing_ground`, or `uncertain`,
   stop for the user.

Use `narrow_scope`, `revise_spec`, or `revise_plan` only when the correction
follows directly from confirmed context. `strategic_documents_updated`
requires explicit user-confirmation evidence unless it mechanically copies
confirmed strategic material.
