# Bounded Diagnosis And Repair

Read this reference when a workflow command, handoff, phase, or state transition
behaves incorrectly. Diagnose only far enough to classify the failure and
choose a safe disposition. Bound inspection to the failing command, active
playbook and step, supplied artifacts, blackboard and baton state, relevant
sanitized logs, and installed CAFE version.

First apply `supervision_and_recovery.md`. Enter diagnosis only when its
non-intervention envelope has been left and the selected disposition requires
classification of incorrect or ambiguous behavior. When the same failure keeps
returning without a concrete reason another retry will differ, use the
supervision reference's consultation boundary before recommending another
retry. Treat a materially different visible failure as a new incident.

## Classification checklist

- [ ] Reproduce read-only. A focused `--single-step` run is permitted only when
  continuous execution itself is the suspected failure boundary, no user-owned
  task is pending, and existing explicit action-specific authority covers that
  exact diagnostic execution. Treat it as a diagnostic override and never
  persist it as a contract change without user confirmation.
- [ ] Rule out project configuration, malformed project artifacts, stale
  installed skills, CLI/model mismatch, transient provider/network failures,
  rate limits, and an agent failing an otherwise valid contract.
- [ ] Preserve every relevant blocker, then choose the highest-priority
  applicable disposition for one next action:
  - **Playbook declarative defect:** wrong graph, artifact binding, intent,
    hook/tool declaration, or planned confirmation gate.
  - **Phase declarative defect:** wrong phase/shared/chat skill contract,
    placeholder, route, or supporting skill resource.
  - **Manager or CAFE core defect:** `use-cafe-workflow`, CLI/runtime Python,
    workflow state machinery, or host execution.
  - **Unconfirmed or transient:** evidence does not distinguish product behavior
    from environment, project, provider, or agent behavior.

Do not turn a bounded incident into open-ended framework refactoring.

## Declarative repairs

For a playbook defect:

- activate `write-cafe-playbook`;
- edit only `.cafe/playbooks/`, or `src/cafe/data/playbooks/` when the authorized
  repository is CAFE;
- run its strict validation;
- if confirmation gates changed, rerun
  `cafe playbook confirmation-gates <id>` and reconfirm the kickoff contract.

For a phase defect:

- activate `write-cafe-phase`;
- edit only `.cafe/skills/`, or `src/cafe/data/skills/` when the authorized
  repository is CAFE;
- run its strict validation.

Never hand-edit generated artifacts, installed packages, or global skill copies as
the source fix. Commit bundled source changes in the CAFE repository; configured
hooks synchronize installed copies. CLI startup performs a per-machine
fingerprint repair. If synchronization fails, recover explicitly with:

```bash
python3 <skill-dir>/scripts/sync_helper_with_preflight.py \
  --cli <approved-cli> <approved-skill>
```

Use only the user's exact approved scope and require the successful post-change
receipt; do not diagnose by bypassing the wrapper with a direct sync command.

Do not use writer skills to change manager/meta skills, CAFE runtime Python,
workflow state machinery, or host infrastructure. Do not invent a
`write-cafe-driver` skill.

## Manager and core defects

Do not self-modify a manager or core defect unless the user explicitly authorizes
that source change. Stop before an unsafe or contract-bypassing workaround and
recommend following or opening an issue at
<https://github.com/luyotw/cafe/issues>.

Before recommending a new issue:

- [ ] Search open and closed issues read-only for an existing match.
- [ ] If none exists, prepare a sanitized draft containing CAFE version,
  CLI/model, playbook/step/intent, exact command, expected and actual behavior,
  minimal reproduction, relevant logs, and any safe workaround.
- [ ] Remove credentials and private project data.
- [ ] Do not create, comment on, or close an upstream issue without explicit
  user authorization.

For unconfirmed or transient failures of a phase agent, follow the bounded retry
rules in `supervision_and_recovery.md`. Eligible stopped executions use its
three same-session retries, 30 seconds apart, before a user handoff. Ineligible
or exhausted interruption tasks remain user-owned; a diagnostic recommendation
does not expand that exception or grant retry authority. Continue through a workaround only through an existing legal
task, input, correction, or authorization path, and only when explicit
action-specific authority permits it, it is reversible and within mandate, and
it preserves the kickoff contract.

## Explicit saved-evidence repair

Normal completion passes required native/readiness gates before publishing its
terminal audit or closing a retained untrusted iteration. During publication,
`workflow_completion_trusted` may retain an exact `{workflow_id, sequence}`
receipt for its reserved existing audit slot. A reservation alone is incomplete:
the owning iteration reader requires the matching committed completion record,
selected iteration path and original control hash. Marker read/size/write failure
publishes no successful terminal; an absent terminal record keeps that iteration
retryable. A valid committed receipt remains sufficient if optional marker
compaction fails. Do not manually delete the marker or interpret provider exit as
completion.

Before binding that receipt, the runtime measures the complete serialized terminal
audit envelope, including its message, data, patch and newline, against the existing
256 KiB receipt-reader bound. Over-capacity publication leaves the original control
bytes and identity intact, without a terminal or advanced handoff. Required receipt
authentication after commit is also a completion gate; its failure cannot be treated
as an optional compaction failure. Reenter through the existing authorized route
after the concrete obstruction is resolved, preserving the original budget and
saved evidence rather than replaying work or raising limits.

Existing legal runtime reentry revalidates the prepared original artifacts,
outbound handoff, capability receipts and required gates before completing a
partial publication. It stops at the declared handoff without rerunning the
provider, hooks or publication, answering a task, or resetting budgets. A pending
human decision or conflicting control/artifact identity cannot be repaired by
guessing success; use the existing authorized handoff.


Ordinary execution entry automatically reconnects an unfinished original
interruption preparation before user-wait routing or provider execution. It
restores the same pending decision and stops; it does not answer or retry it.
The delivery consumer can restore an omitted full checkpoint only from one
complete checkpoint saved in the producing iteration, with the same session,
context, authority, current content, endpoint and required independent review.
It then passes the existing completion gates. Missing, conflicting or stale
proof follows the original correction/human route; no standalone readiness
repair command is supported.

`cafe repair` is a supported local machine-evidence operation. It does not run a
phase or continue the workflow. Use the current authorized issue/worktree and
select one exact source step and iteration. Preview is read-only by default;
`--execute` applies only the verified repair. This coordination lease grants no
permission to answer a HumanTask, change a CLI/model/session, or publish again.

```bash
cafe repair --issue example --kind interruption --step build_custom --iteration 1 --execute
cafe repair --issue example --kind pr-result --step ship_custom --iteration 1 \
  --execution-context-file .cafe/issues/example/saved-execution-context.json --execute
```

Every execute holds the existing nonblocking advancement lease, verifies worker
quiescence, then uses the existing workspace lease nonblocking before store transactions.
Terminal launch status alone does not prove quiescence while its PID is live or
reused; ownership uncertainty refuses. A live or
uncertain launch, another repair, unsafe aliases, ambiguous identity, or an
unresolved human decision refuses the applicable operation. Do not kill a
worker, change ownership, or edit generated state to pass these prerequisites.
Status/show/inspection never deduplicate iteration state.

| Kind | Verified source and local result | Next action |
| --- | --- | --- |
| `interruption` | Genuine original interruption and handoff/prepared identity; materialize or reconnect exactly one pending HumanTask and coherent user owner. Completed/cancelled/ambiguous tasks refuse. | Inspect that pending task and present its existing choices to its owner; repair supplies no answer. |
| `pr-result` | Original attempt, saved context/checkpoint and a genuine retained execution receipt, then exact trusted PR observations. Add reconciliation history and effective outcome to the existing result record. | Inspect `reconciliation.effective_status` and the preserved original attempt; never repeat push/create to resolve an unknown result. |

PR repair requires the original saved resolved execution context,
with the selected workflow/issue/root, authority, revision, content and endpoint
still provable. A reference or receipt ID cannot replace a full checkpoint.
Missing machine schema enters existing bounded producer correction; changed
execution authority or a real decision retains its existing human route. Repair
never fabricates a checkpoint/terminal/review/approval or answers a pending task.

JSON nesting is bounded to 64 levels. Audit/task attribution is bounded to 16 MiB of retained evidence (at most 100,000 audit
slots). Unsafe files, malformed evidence and ambiguous attribution refuse
without changing source bytes. These repair-only bounds do not change the
ordinary 1,048,576-byte reader, 983,040-byte workspace writer or 65,536-byte
workspace reserve. Execution artifacts and observation/result records retain
their existing 256 KiB bound; PR observation history remains bounded to 128
distinct fingerprints.

A new fingerprint at that maximum refuses before writing and preserves the
admitted record. Revisiting a retained fingerprint applies its freshly observed
outcome without duplicating history; `reconciliation.current_fingerprint`
identifies the currently applied observation. Older records without this field
use their last observation. An immediate identical repeat is a no-op and
preserves the original bytes; its exit status still reports 3 for unresolved
or 5 for mismatch, rather than implying verified success.

Historical PR attempts containing only `delivered`, `status`, `authority_digest`
and `commit` do not need future fields. A uniquely matching original context,
endpoint/checkpoint and independently saved capability request/execution receipt
or completed approval's `capability_approval.receipt.execution` can prove their
binding. Missing receipts, competing candidates, changed request/workflow/
correlation/remote/repository/branch/SHA/PR identities refuse. Current HEAD or a
convenient URL never supplies missing original intent. Reconciliation uses only
existing authorized GitHub reads, supplements missing CLI identity with matching
REST evidence, and retains conflicts. Merged state passes only an original
confirmed condition that permits it; this performs no merge or approval consumption.

JSON outcomes are `proposed`, `repaired`, `no-op`, `refused`, `interrupted`,
`unresolved`, `verified` or `mismatch` as applicable. CLI exits are 0 for successful
preview/repair/no-op/verification, 2 refusal, 3 unresolved, 4 interrupted
persistence and 5 mismatch. Read diagnostics retain subprocess return codes.
An interrupted persistence result is not proof of rollback: inspect durable
bytes/audit and explicitly reenter the same selection. Preserve original
session/model/CLI, correction budget, usage, completion trust, receipt and
approval history; none is reset by repair.
