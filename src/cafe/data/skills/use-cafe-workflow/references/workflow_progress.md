# Workflow Progress Diagram

Read this reference before every user-visible Driver reply. For an initial
kickoff, `format_kickoff_contract.py` owns the complete response and places the
verbatim progress diagram at its end. For every other question, progress update,
error, and completion message, the final block must be the verbatim stdout of
`scripts/render_workflow_progress.py`. Do not hand-write, translate, reorder,
trim, or otherwise repair its diagram. If rendering fails, report the renderer
error and do not invent progress.

The renderer reads the effective playbook, including `issue.yaml` overrides,
plus existing blackboard, iteration, HumanTask, and confirmed Driver-contract
records. It never starts or resumes a workflow and never writes runtime or
Driver state. Calling it is presentation, not a poll required by supervision;
invoke it only when a user-visible response is already due. `action: yield`
still ends the current turn without an additional inspection.

Use the effective conversation locale with `--locale`. Traditional Chinese is
selected by `zh-TW` or `zh-Hant`; unsupported locales fall back to English.
Step keys are always preserved exactly. `deliver` and `cleanup` are required
Driver closeout items, not runtime phases, and always appear after the playbook
phases. A playbook phase with the same name remains a separate unqualified node.

Stdout is a compact vertical execution spine. Each node carries a readable text
status symbol plus its localized status text. Renderer-owned status markers use
text presentation, never emoji presentation; ambiguous Unicode symbols are
forced to text with variation selector 15. Proactive-review and confirmation
checkpoints immediately follow their owning phase. The default diagram is a
latest-state projection: it shows each phase's newest durable status and the
Driver-review or user-confirmation checkpoint currently represented for that
phase. The default projection never adds correction arrows or a historical
trail; later iteration evidence supersedes earlier states. If a phase's newest
durable state is itself returned, the phase line uses the returned symbol and
status. The
renderer intentionally omits raw `on`/`allowed_goto` route dumps and a separate
always-on legend. The effective graph is still authoritative for phase
traversal, status, and correction interpretation. Sibling branches and phases
unreachable from the entry point are separated rather than joined by a false
spine edge. Omitting route declarations from the presentation does not change
runtime routing.

Driver-only display state is one JSON object with only these fields:

```json
{
  "proactive_review": {"spec": "completed", "plan": "in_progress"},
  "deliver": "pending",
  "cleanup": "pending"
}
```

Allowed states are `pending`, `in_progress`, `completed`, `returned`,
`awaiting_confirmation`, `skipped`, `blocked`, and `unknown`. Supply
Both `deliver` and `cleanup` are required for every established-workflow render.
Supply `proactive_review` only for phases whose confirmed
`proactive_review.phase_decisions` entry is `required`. Omitted displayed
proactive-review state is `unknown`, including after a session boundary. The
JSON cannot set phase or HumanTask status, confirmation ownership, return
evidence, or gate outcomes. It is never persisted and grants no confirmation,
capability, or external-operation authority.

## Minimal calls

Kickoff uses `format_kickoff_contract.py`; that formatter invokes this renderer
itself with `deliver` and `cleanup` set to `unknown` and makes its output the
final kickoff block. Present the complete formatter stdout and do not append a
second diagram.

For an ordinary running update:

```bash
python3 <skill-dir>/scripts/render_workflow_progress.py \
  --project-root <repo> --issue-dir <repo>/.cafe/issues/<issue> \
  --locale zh-TW --driver-state \
  '{"proactive_review":{"develop":"in_progress"},"deliver":"pending","cleanup":"pending"}'
```

For a waiting-confirmation question, use the same call after reading the
already-pending task. Do not pass a confirmation status; the renderer obtains
it from `human_tasks.json`:

```bash
python3 <skill-dir>/scripts/render_workflow_progress.py \
  --project-root <repo> --issue-dir <repo>/.cafe/issues/<issue> --locale en \
  --driver-state '{"deliver":"unknown","cleanup":"unknown"}'
```

For a formal return, again pass no return override. The completed task outcome
or durable transition updates the latest phase/checkpoint status; the renderer
does not add a historical return arrow:

```bash
python3 <skill-dir>/scripts/render_workflow_progress.py \
  --project-root <repo> --issue-dir <repo>/.cafe/issues/<issue> --locale zh-TW \
  --driver-state '{"deliver":"unknown","cleanup":"unknown"}'
```

For completion and Driver closeout reporting, provide both required closeout
values:

```bash
python3 <skill-dir>/scripts/render_workflow_progress.py \
  --project-root <repo> --issue-dir <repo>/.cafe/issues/<issue> --locale en \
  --driver-state '{"deliver":"completed","cleanup":"pending"}'
```

On a resumed Driver session, rebuild the ephemeral JSON from evidence available
in that session. Use `unknown` when it cannot be verified; never copy chat memory
as proof. After `cafe close` moves issue data, pass the exact existing archive
directory reported by the lifecycle command, for example:

```bash
python3 <skill-dir>/scripts/render_workflow_progress.py \
  --project-root <repo> \
  --issue-dir ~/.cafe/projects/<project-path>/archived/<issue> \
  --locale zh-TW --driver-state '{"deliver":"completed","cleanup":"completed"}'
```

An explicit archive path is read exactly like an active issue path. If neither
an effective playbook nor workflow contract can be found, preserve the
renderer’s short “workflow not established” output; never substitute a success
diagram.
