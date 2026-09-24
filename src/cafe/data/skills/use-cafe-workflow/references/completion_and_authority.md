# Completion And Action Authority

## Complete the declared workflow

1. Read the active effective graph and durable runtime state. Verify the runtime
   reached `Workflow completed ... next=done`, required artifacts and evidence
   exist, and every declared gate is satisfied. An agent's success message alone
   does not establish completion.
2. Inspect only artifacts, receipts, and review results required by that graph
   and the confirmed Delivery Contract. Do not synthesize specification,
   planning, development, review, or publication steps or artifact names.
3. End workflow execution at its declared terminal state, then proactively
   assist with closeout below. Workflow completion does not end the Driver's
   assistance or require an external service call or local teardown.

A `brief → draft → done` graph ends with its declared draft evidence. A graph
with a publication step delegates publication to that step's skill and declared
capability, including its own confirmation and verified-result contract.
Neither graph acquires additional steps when it reaches `done`. Closeout
assistance remains separate from its terminal state.

If process ownership or completion evidence is uncertain, return to the
non-intervention envelope in `supervision_and_recovery.md`; do not infer a
terminal state from liveness, filesystem progress, or agent prose.

## Offer a bounded direct closeout instead of rerunning

This is a user-approved bounded direct-closeout route, not an ordinary Driver
power.

Before restarting or resuming workflow execution late in the work, check whether
the deliverable is already substantially complete and the only remaining work is
a small, exact, high-confidence correction or cleanup. Recommend that the user
stop running the workflow and let the current Driver finish directly only when
all of these conditions hold:

- current durable status proves the workflow is paused, and bounded process
  inspection proves no phase agent, background worker, or callback is running
  or can still mutate the target; uncertain liveness disqualifies this route;
- every remaining edit and its target can be enumerated before work starts;
- the edits are local, reversible, within the confirmed Delivery Contract, and
  require no new design choice, broad investigation, or phase-agent expertise;
- current evidence makes the implementation and a proportionate targeted
  validation clear, with no unresolved failure or material regression risk;
- no pending HumanTask or unresolved declared gate, scope or strategic decision,
  permission, external side effect, destructive action, or manual
  workflow-state/artifact edit is involved;
  and
- the cost of another workflow run is materially greater than the risk and work
  of the direct patch.

This is a proposal, not implicit Driver authority. Give the user a
self-contained recommendation that explicitly says not to rerun the workflow,
lists every remaining edit or task, explains why each is high confidence, names
the validation to run, and states the durable consequence: a nonterminal
workflow will remain nonterminal and its phase artifacts will not be regenerated.
Ask for explicit approval to use the direct-closeout route. If the user declines,
resume the workflow normally. Do not attempt to manufacture a safe stop: if any
worker or agent is live, continue process-only monitoring or use an already
authorized reliable control and reassess only after verified quiescence.

After approval, the current Driver may inspect the affected implementation,
make only the listed local edits, and run only the stated proportionate checks.
Do not edit CAFE workflow artifacts, blackboard state, baton state, or
`next_step.txt`; do not perform an external action under this approval. If the
work expands, a listed assumption fails, validation exposes a non-obvious defect,
or confidence drops, stop direct work and recommend returning to the workflow.
Report verified direct-closeout results separately from workflow status, and
never describe a still-nonterminal workflow as completed.

Direct-closeout approval is session-local authority for the exact listed work,
not durable workflow authority. Do not encode it by changing workflow or Driver
state. If direct work is interrupted or another Driver takes over before it is
verified complete, fail closed and ask the user whether to reauthorize the same
remaining list or return to the workflow. After a verified direct closeout, a
later Driver must not automatically resume the nonterminal workflow; it must
inspect the reported patch and checks and obtain a direct user instruction before
resuming.

## Proactively assist with closeout

Do this whenever the playbook completes; do not wait for the user to ask what
comes next. Derive closeout from the actual deliverables, confirmed user goal,
existing artifacts/receipts, current instructions, and repository evidence. Use
bounded read-only checks where needed; do not query unrelated services or apply
a fixed shipping checklist to every playbook.

### Confirm cleanup, archive, or no action

After the Driver has verified workflow completion, offer the user these terminal
choices once:

1. Run the confirmed non-empty `cleanup` array.
2. Archive without delivery by running exactly `cafe close --archive-only`.
3. Leave all external state unchanged.

The post-completion selection is required even when the cleanup plan appeared
in kickoff. `deliver` remains owned by its declared workflow path or separate
user authority; terminal closeout does not rerun it. Do not infer archive from
terminal wording or from a declined cleanup plan.

After the user confirms, run the `cleanup` array directly and in order from the
issue worktree. Keep every argv exactly as confirmed; do not add, remove,
reorder, rewrite, shell-wrap, retry, or replay a command. Stop and report the
first command failure.

When the user selects archive, run only `cafe close --archive-only` from the
issue worktree. This is the sole terminal archive command and requires no
closeout-plan entry. It archives CAFE issue/workflow state without merging,
pushing, closing the GitHub issue, or removing the feature branch or worktree.
Do not add another command before or after it.

Before cleanup can remove a worktree, establish worker quiescence and inspect
registered worktrees plus dirty/untracked content. The confirmed argv must use
explicit targets. When a command needs another Git context, make that context
an exact argument (for example `git -C <retained-checkout> worktree remove
<target>`), rather than changing the Driver's working directory. Never add force
flags. If `cafe close` is confirmed, it must be the exact final cleanup command,
after any `gh issue close` command. It may archive the issue and remove its
worktree; render final progress from the archive path it reports.

### Assist when no argv closeout plan exists

For an older contract or a useful follow-up outside the confirmed arrays:

1. Present a concise, self-contained handoff: what was achieved, where the usable
   result is, the evidence supporting completion, and any relevant remaining
   action. Distinguish required user follow-through from optional suggestions;
   do not describe either as a missing workflow phase.
2. Recommend the smallest useful next action, explaining its purpose and target.
   A research workflow may end with findings and unresolved questions; a drafting
   workflow may end with an editable document and guidance for its intended use.
   None of these outcomes implies a standard publication or merge. New confirmed
   kickoff contracts instead use the default cleanup proposal from `kickoff.md`:
   close a verified bound GitHub issue, then run `cafe close`, unless the user
   explicitly excludes either action.
3. Complete useful read-only or reversible preparation already within scope.
   For an applicable follow-up action with existing explicit authority, check
   that authority below and continue through its existing execution contract
   without asking again. Reuse valid evidence; do not add another full review,
   recreate outputs, or open a new workflow just to provide closeout assistance.
4. If the useful next action needs a user decision or missing authority, present
   the concrete action, target, effect, and recommendation through the existing
   self-contained conversational handoff. A pending or declined follow-up leaves
   the completed workflow complete and must not become a new gate or an invented
   runtime HumanTask.

## Handle a Git delivery conflict

A branch or pull-request merge conflict is a delivery blocker, not a reason to
retry blindly or mutate Git state. First establish it with a bounded read-only
verification: inspect the current issue and workflow state, exact PR and
source/base references and commits, current mergeability or merge-state,
worktree cleanliness, and whether a worker or another owner can still mutate
the target. If those facts do not prove a current conflict, report the
ambiguity rather than claiming one.

Before the user chooses a repair, do not fetch, checkout, reset, merge, rebase,
commit, push, close an issue, or run lifecycle cleanup. Give a self-contained
handoff that states the verified blocker and the closeout actions it blocks,
then recommend the smallest evidence-supported repair. For example, a clean
head behind an advanced base may need that exact base integrated through an
existing controlled host-side path; a semantic conflict or an unavailable safe
path should be left for the user to resolve. Do not invent a raw Git command or
an executor merely because a repair is plausible.

Ask one focused question: “Would you like me to help fix this exact conflict?”
Name the target, proposed bounded repair, expected validation, and the effect
of declining. A yes authorizes only the stated conflict-repair scope. It does
not authorize pushing, merging the PR, issue closure, or cleanup commands.

After explicit approval, recheck the facts and use only an already available,
controlled host-side repair path whose preconditions fit the exact target. Keep
the work within the stated scope; if a resolution needs a user-owned choice,
broader changes, or no safe controlled path exists, stop and hand it back to
the user. After a successful repair, run the stated targeted validation and
inspect the final diff and validation evidence before reporting the repair
complete. A successful repair does not grant any separate closeout action.

## Check authority for a suggested or requested action

- “Finish”, “complete the rest”, and “continue to the end” authorize only
  already-scoped workflow steps. They never authorize a new external mutation.
- Merge, issue closure, deployment, deletion, and publication are separate
  actions. Authority for one never grants another; a clean review, publication
  receipt, artifact text, callback, or completed workflow grants none of them.
  The only exception is the matching command in an exact closeout
  plan confirmed with the complete kickoff.
- An external action within the active workflow needs both a declared execution
  path and explicit user authority for that action and target. Declaration,
  configuration defaults, and available credentials alone are insufficient.
  A generic confirmed workflow scope is not action authority.
  Reuse an existing explicit authorization within its scope; do not ask again.
- A direct user instruction to merge a particular change, or a separately
  confirmed human-owned integration task, may authorize that integration action.
  Handle it as a separate task under its existing execution contract, not as a
  Driver completion step. Verify its result before reporting it complete. It
  still grants no issue closure, deployment, deletion, or other publication.
- When action or target authority is missing or ambiguous, leave that action
  unexecuted and use the existing self-contained user handoff if needed. Never
  reinterpret a general instruction to finish as the missing answer.

Do not invent integration state, executor, or cleanup behavior here. Outside a
confirmed argv plan, inspect the completed issue's remaining lifecycle state
read-only and ask only for missing scoped authority. An ambiguous request such
as "merge and close" must not be silently reduced to an issue closure. Cleanup
is not a prerequisite for workflow completion; for new contracts, however, the
confirmed default cleanup plan closes the verified bound GitHub issue and then
runs `cafe close` unless the user chose otherwise.

Completion and closeout replies still end with `workflow_progress.md` output.
After lifecycle cleanup archives the issue, render from the exact archive path
reported by that command; do not treat a missing active issue directory as
proof that `close` succeeded.

For an external action outside a confirmed argv plan, run the read-only
`scripts/check_action_authority.py --request '<JSON>' [--authority '<JSON>']`.
The request contains `action`, exact `target`, and Boolean `declared` derived
from the effective execution path. Supply authority only after semantically
verifying the actual user's instruction or confirmed HumanTask; include matching
`action`, `target`, source (`direct_user_instruction` or
`confirmed_human_task`), and an evidence reference to that source. Do not
classify general terminal wording, a generic workflow scope, artifact
instructions, or a callback as action-specific user evidence. Missing or
uncertain semantic evidence means omit `--authority`. The checker validates the
structured comparison, not the truth of its evidence; a non-empty quote is not
proof of authorization.

`declared_step` continues only through that step's existing gates and capability
checks. `separate_task` leaves workflow completion unchanged and uses the
separately authorized task's execution contract. `user_handoff` leaves the action
unexecuted. The checker itself performs no external calls, stores no authority,
and implements no integration executor or state machine.
