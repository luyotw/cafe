# Workflow Progress Diagram

Read this reference before every user-visible Driver reply. The final block of
every kickoff, question, progress update, error, and completion message must be
the verbatim stdout of `scripts/render_workflow_progress.py`. Do not hand-write,
translate, reorder, trim, or otherwise repair its diagram. If rendering fails,
report the renderer error and do not invent progress.

The renderer reads the effective playbook, including `issue.yaml` overrides,
plus existing blackboard, iteration, HumanTask, and confirmed Driver-contract
records. It never starts or resumes a workflow and never writes runtime or
Driver state. Calling it is presentation, not a poll required by supervision;
invoke it only when a user-visible response is already due. `action: yield`
still ends the current turn without an additional inspection.

Use the effective conversation locale with `--locale`. Traditional Chinese is
selected by `zh-TW` or `zh-Hant`; unsupported locales fall back to English.
Step keys are always preserved exactly. `deliver` and `close` are optional
Driver closeout items, not runtime phases: include each only with its explicit
flag. A playbook phase with the same name remains a separate unqualified node.

Stdout is a compact vertical execution spine. Each node carries its localized
status inline; proactive-review and confirmation checkpoints immediately follow
their owning phase, and durable correction arrows stay with the phase they
re-enter. The renderer intentionally omits raw `on`/`allowed_goto` route dumps
and a separate always-on legend. The effective graph is still authoritative for
phase traversal, status, and correction interpretation. Sibling branches and
phases unreachable from the entry point are separated rather than joined by a
false spine edge. Omitting route declarations from the presentation does not
change runtime routing.

Driver-only display state is one JSON object with only these fields:

```json
{
  "proactive_review": {"spec": "completed", "plan": "in_progress"},
  "deliver": "pending",
  "close": "pending"
}
```

Allowed states are `pending`, `in_progress`, `completed`, `returned`,
`awaiting_confirmation`, `skipped`, `blocked`, and `unknown`. Supply
`proactive_review` only for phases whose confirmed
`proactive_review.phase_decisions` entry is `required`. Omitted displayed
Driver state is `unknown`, including after a session boundary. The JSON cannot
set phase or HumanTask status, confirmation ownership, return evidence, or gate
outcomes. It is never persisted and grants no confirmation, capability, or
external-operation authority.

## Minimal calls

Kickoff uses `format_kickoff_contract.py`; that formatter invokes this renderer
itself and explicitly displays both closeout items. Do not append a second
diagram.

For an ordinary running update:

```bash
python3 <skill-dir>/scripts/render_workflow_progress.py \
  --project-root <repo> --issue-dir <repo>/.cafe/issues/<issue> \
  --locale zh-TW --driver-state \
  '{"proactive_review":{"develop":"in_progress"},"deliver":"pending","close":"pending"}' \
  --show-deliver --show-close
```

For a waiting-confirmation question, use the same call after reading the
already-pending task. Do not pass a confirmation status; the renderer obtains
it from `human_tasks.json`:

```bash
python3 <skill-dir>/scripts/render_workflow_progress.py \
  --project-root <repo> --issue-dir <repo>/.cafe/issues/<issue> --locale en
```

For a formal return, again pass no return override. The completed task outcome,
continuation, current iteration, and durable transition produce the return edge:

```bash
python3 <skill-dir>/scripts/render_workflow_progress.py \
  --project-root <repo> --issue-dir <repo>/.cafe/issues/<issue> --locale zh-TW
```

For completion and Driver closeout reporting, show only the closeout items that
actually apply and provide their current display values:

```bash
python3 <skill-dir>/scripts/render_workflow_progress.py \
  --project-root <repo> --issue-dir <repo>/.cafe/issues/<issue> --locale en \
  --driver-state '{"deliver":"completed","close":"pending"}' \
  --show-deliver --show-close
```

On a resumed Driver session, rebuild the ephemeral JSON from evidence available
in that session. Use `unknown` when it cannot be verified; never copy chat memory
as proof. After `cafe close` moves issue data, pass the exact existing archive
directory reported by the lifecycle command, for example:

```bash
python3 <skill-dir>/scripts/render_workflow_progress.py \
  --project-root <repo> \
  --issue-dir ~/.cafe/projects/<project-path>/archived/<issue> \
  --locale zh-TW --driver-state '{"deliver":"completed","close":"completed"}' \
  --show-deliver --show-close
```

An explicit archive path is read exactly like an active issue path. If neither
an effective playbook nor workflow contract can be found, preserve the
renderer’s short “workflow not established” output; never substitute a success
diagram.
