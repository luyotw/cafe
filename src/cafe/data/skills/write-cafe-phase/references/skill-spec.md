# CAFE Workflow Skill Specification

This specification is the authoring reference for CAFE skills. It is written
in en-US because it is an authoritative contract consumed by authors,
validators, and the workflow runtime. If an existing skill conflicts with this
reference, this reference wins and the skill should be repaired at the same
time.

## Contents

- Sections 1–3: skill types, catalog rules, frontmatter, and repair boundaries.
- Sections 4–6: phase structure, placeholders, handoff, and confirmation gates.
- Sections 7–12: shared rules, iteration, resources, language, and playbook binding.
- Section 13: acceptance checklist.
- Sections 14–15: plan-to-execute ownership and forward-only plan chains.
- Section 16: supporting domain-skill selection.
- Section 17: interruption-safe checkpoint and resume behavior.
- Section 18: the current artifact contract.

## 1. Skill Types and Boundaries

CAFE has four skill types:

| Type | Boundary | Purpose | Examples |
| --- | --- | --- | --- |
| **Phase skill** | Internal | Binds one playbook step to a workflow procedure. | `cafe-spec`, `cafe-plan`, `cafe-develop`, `cafe-review`, `cafe-pr` |
| **Shared skill** | Internal | Supplies reusable rules or tools across phases. | `cafe-workflow-common`, `cafe-github_sync`, `cafe-common-chat-handoff` |
| **Chat skill** | Internal | Handles a named change type in `cafe chat`. | `cafe-chat-develop-change`, `cafe-chat-plan-revision` |
| **Driver or meta skill** | External | Guides a user or outer agent and is not injected into a workflow step. | `use-cafe-workflow`, `write-cafe-agent`, `write-cafe-phase`, `write-cafe-playbook` |

A skill belongs to exactly one type. Internal skills are installed by the
runtime into a worktree-local CLI-native directory and keep their `cafe-`
prefix. External skills are installed by the user and must carry clear CAFE
context in their names.

## 2. Storage and Discovery

- Store a skill at `<root>/skills/<skill-name>/SKILL.md`; project skills use
  `.cafe/skills/`, and builtin skills use `src/cafe/data/skills/`.
- The directory name must equal frontmatter `name`. Builtins that violate this
  rule fail validation.
- Legacy internal names are aliases in the loader only; do not maintain a
  second copy of the content.
- A project override uses the canonical prefixed name and is committed with
  the project playbook.
- A skill directory contains only `SKILL.md`, `references/`, `scripts/`, and
  `assets/`.

Internal skills always use the `cafe-` prefix. The runtime installs them into
the worktree-local native skill directory, installed without renaming them, so the
playbook name, prompt invocation, installation directory, and CLI invocation
remain identical. External driver and meta skills do not use that prefix, but
their names must carry clear CAFE context. Custom playbook skills belong in
`.cafe/skills/` beside the versioned playbook; global skills are for deliberate
cross-project reuse. Do not use generic or deprecated names such as `review`
or `draft` for a custom skill.

Phase skills use `snake_case` after the `cafe-` prefix, while shared and chat
skills use `kebab-case`. External driver and meta skills remain unprefixed but
must carry clear CAFE context.

## 3. Frontmatter and Repair Boundary

Use frontmatter like this:

```yaml
name: <directory-name>
description: "When this skill should be used"
version: 1.0.0
```

Frontmatter is stripped during activation; keep executable instructions in the
body rather than in frontmatter.

The description states when to use the skill, not a list of everything it
contains. Runtime metadata must remain provider-neutral execution-requirement metadata: do not name a CLI
provider, model, pricing tier, or mutable external service in execution
requirements. Do not name a CLI provider, model, pricing tier in a provider-neutral
execution-requirement declaration.
When variants declare execution requirements, conservatively aggregate every
declared variant.

`write-cafe-phase` owns only source-of-truth phase, shared, and chat skills and
their supporting resources. It may edit `.cafe/skills/<skill-name>/` or the
authorized builtin source under `src/cafe/data/skills/<skill-name>/`. It must
not edit generated issue artifacts, installed CLI copies, global copies,
driver/meta skills, CAFE core/runtime code, playbooks, or host infrastructure.
When a defect is outside this boundary, return a CAFE core-defect diagnosis or
return the classification to the driver for `write-cafe-playbook`; do not
create an implicit `write-cafe-driver` fallback.

Supporting-skill selection is authoring-time work. Runtime does not search the
network, download mutable latest content, or guess substitutes. External issue
creation, comments, or closing require explicit user authorization.

### Workflow metadata contract

Phase skills may declare provider-neutral workflow metadata in frontmatter.
Runtime-owned files, blackboard state, routing, and external side effects stay
outside this block. A declaration describes what the skill needs to execute;
it does not select a provider or model.

```yaml
workflow:
  execution_profile:
    workload: research
    reasoning: high
    risk_domains: [source-quality, conflicting-evidence]
    fallback_strength: equivalent_or_stronger
  required_tools:
    - "Bash(cafe verification check:*)"
  prompt_inputs:
    - artifacts: [research_notes]
      placeholder: evidence_file
      required: true
  prompt_references:
    optional_evidence_instruction: optional_evidence_instruction.md
  checklist:
    context_references:
      xml_questions_instruction: xml_questions_instruction.md
    variants:
      - when: {iteration: 1}
        sections: [{reference: execution_first.md}]
      - when: {artifact_present: [editor_feedback]}
        sections: [{reference: execution_feedback.md}]
    include_role_guidance: true
    compact_agent_guidance: false
  output_templates:
    catalog: research-report
```

Every phase skill declares `workflow.execution_profile`. The workload is one
of `general`, `requirements`, `planning`, `implementation`, `review`,
`publication`, `operations`, `research`, or `content`; reasoning is `routine`,
`standard`, or `high`; risk domains are unique stable tokens; and fallback
strength is `equivalent` or `equivalent_or_stronger`. When a selector has
multiple variants, aggregate every variant before runtime execution.

`required_tools` lists only tools that the normal path cannot execute without.
Every playbook step selecting the skill must grant them through `allowed_tools`.
An exact grant or an intentionally broader grant satisfies the declaration;
optional diagnostics do not belong in this list.

### Human-task policy contract

When a phase may pause for a person, declare its reusable policy under
`workflow.human_tasks`. The playbook binds that policy to a trigger and owns
routing; the skill owns wording and answer validation; runtime owns files,
state, and baton mutation.

```yaml
workflow:
  human_tasks:
    - id: output-review
      pattern: confirm_output
      prompt: Review the result and choose how to continue.
      input_schema: decision
      decisions:
        - id: confirm
          label: Confirm and continue
        - id: revise
          label: Request revision
          requires_feedback: true
          correction: true
```

Valid policy patterns and schemas are `confirm_output`/`decision`,
`answer_questions`/`answers`, `revision_feedback`/`feedback`,
`no_changes_needed`/`decision`, and `select_next_step`/`target`. A decision
with `requires_feedback: true` requires feedback but does not define routing;
`correction: true` marks a repair choice that remains routable while the
current output is invalid. A decision with `requires_target: true` must bind
declared `allowed_targets`. Answer policies may use inline questions or
`questions_from_xml: true`; target policies declare `allowed_targets`.
Optional feedback is the only use of `required: false`. Keep policy IDs
stable, and use `correction_guidance` for actionable invalid-input messages.

## 4. Phase Skill Structure

Use this order and omit sections that do not apply:

```markdown
# <Title>

## Role
<The fixed role sentence.>

## Context
<Only artifacts supplied by the playbook.>

## Available scripts
- `scripts/<name>.sh` — one-sentence purpose

## Instructions
- Use procedural, verb-first instructions.

## Output
Write <artifact> to: {output_file}

## Handoff
Write next-step baton for this result; the runtime updates the blackboard.
```

Every phase skill has `## Role` and `## Handoff`. `## Context` lists only
declared inputs. `## Output` contains the fixed output path instruction. Route
decisions belong in `## Instructions`; shared baton schema does not.

Use these exact structural lines in a phase skill unless the section is not
applicable:

```markdown
## Role
Read your agent file: {agent_file}

## Handoff
Write next-step baton for this result; the runtime updates the blackboard.
```

The role and handoff lines are part of the authoring contract, not decorative
copy. A phase may add role-specific instructions below them, but it must not
replace or paraphrase these activation and ownership boundaries.

## 5. Placeholder Contract

Placeholders are literal text substitutions performed at activation. They do
not support conditionals or expressions. Runtime-owned placeholders include:

| Placeholder | Supplied by |
| --- | --- |
| `{agent_file}`, `{output_file}` | Every phase step |
| `{handoff_summary}`, `{blackboard_path}`, `{next_step_path}` | Every phase step |
| `{valid_to_steps}`, `{step_transitions}` | Every phase step |
| Skill-declared input placeholders | The resolved artifact record |
| `{template_file}`, `{template_catalog}` | A skill declaring output templates |
| `{commits}`, `{base_branch}` | Git context when requested |

New artifact placeholders require a metadata and contract update. Do not add a
skill-name branch to `generic_workflow_step.py`.

Prompt inputs resolve candidates in listed order. A required input stops before
agent invocation and reports the missing placeholder and candidate artifacts;
an absent optional input is omitted. Prompt references name files under
`references/` and render only when every placeholder in that reference is
available. Checklist references remain under `references/`; variants are
evaluated in declaration order using bounded iteration, artifact-presence, or
feedback selectors. Role guidance is opt-in, and compact guidance does not
silently add a separator. A template catalog belongs to the owning skill's
`assets/templates/` directory. The selected issue template is read from
`<step>.template` in `issue.yaml`; `auto` exposes the catalog without selecting
a file. Do not infer a template from a phase name, artifact name, or iteration
number.

## 6. Handoff and Confirmation

### Planned User Confirmation Gates

The baton mechanism, JSON schema, legal values, and examples live only in
`cafe-workflow-common`. A phase skill states routing decisions without
duplicating that schema. Use playbook step names and the built-in `user` and
`done` targets; do not assume that a particular playbook contains `pr`.

A planned user approval requires both a phase routing decision and a matching
playbook `on.confirm_output` transition. The confirmation-gates command is the
source of truth for assignable versus mandatory gates. Clarification,
permission, and alignment checkpoints are reactive interruptions, not planned
kickoff gates.

Neither a skill-only pause nor a playbook-only gate is a complete contract. The
phase routes the completed output to `user`, and the bound playbook step uses
`confirm_output: <current-step>` so the approval remains at that step. A
matching binding with `feedback_delivery` is one of the mandatory HumanTask
gates and is mandatory user-owned; it is not assignable to the Driver at
kickoff. The stop contract is step-level.

The phase skill must route a normal approval to `user`, while the playbook
binding supplies the matching `on.confirm_output` transition. A mandatory
HumanTask binding with `feedback_delivery` is user-owned and is not a driver
kickoff candidate. A reusable `revise` decision may require feedback and a
target; the playbook must authorize every target. If the binding is absent or
ambiguous, pause with a configuration error instead of guessing a continuation.

If a phase has a prerequisite decision before its final output, keep both
stages together only when they share ownership, artifact lifecycle, and final
approval. Persist durable stage evidence, mark provisional output as
unconfirmed, and keep it unreachable from downstream execution until
`confirm_output`. A HumanTask prompt must contain the decision context and
validation rules. Split the phase when ownership, artifacts, gates, reuse, or
downstream reachability differ.

### Multi-stage checkpoints within one phase

If a phase has multiple stages with one owner and one final artifact, keep the
stages in one step only when durable stage evidence, resume rules, and a
human-readable checkpoint make the boundaries unambiguous. Split the step when
ownership, artifacts, planned gates, or downstream reachability differ. Treat
iteration selectors as first-entry/resume routing rather than stage identity,
and keep downstream execution unreachable until the final `confirm_output`.

## 7. Shared Rules

Rules that apply to multiple phases belong in `cafe-workflow-common`, with its
Where policies live index updated. A phase references the shared section and
does not copy it. Runtime resolves shared skills from the active playbook's
playbook `skills.workflow` and playbook `skills.chat`; neither mapping should be
implemented as a Python constant.

## 8. Iteration Behavior

Use one skill with bounded first-entry and later-iteration selectors when the
procedure differs only slightly. Use separate skills when the role or output
contract differs materially. The selector distinguishes first entry from
resume; it must not infer a domain stage from an iteration number.

## 9. References and Scripts

References contain details that are needed only under a declared condition.
`SKILL.md` must say exactly when to open each reference; an unreferenced file
is not an active instruction. `references/execution_steps_*.md` files are
ordered procedures. `references/basic_principles.md` holds always-on
repository rules and is projected into `## Basic Principles` when enabled.
Agent-file guidelines hold personal style and role preferences that follow the
agent across phases; they do not replace workflow-owned always-on rules.
References and scripts have explicit activation conditions. Scripts must
declare their activation condition, required inputs, and output or receipt
contract. A script catalog is not activated merely because the file exists.
Skill scripts are deterministic and rerunnable; progress and errors use stderr
and structured results use stdout. Remote mutation runs through a host-side
hook, while the agent prepares local artifacts.

## 10. Chat Skill Structure

Chat skills use:

```markdown
# <Title>
## Use This Skill When
- <trigger>
## Instructions
- <change-specific procedure>
```

Chat handoff, baton, and commit rules remain in the common chat skill.

## 11. Language Convention

Authoritative headings and structural terms are English. A skill may use a
configured user-facing conversation locale for domain prose, but schema names,
paths, artifact keys, Todo rows, status values, and validation messages remain
stable. Any authoritative phase or authoring document changed by this issue
must be en-US.

## 12. Playbook Binding

Skill content does not embed a playbook binding. The playbook step declares the
skill, role, input artifacts, output artifact, tools, hooks, and transitions.
If a phase has planned approval, the step declares `on.confirm_output`; run
`cafe playbook confirmation-gates <id>` after changing a planned gate.

The playbook is also the source of truth for the selected skill, role, input
and output artifacts, tools, hooks, and transitions. Do not encode a playbook
binding in a skill or duplicate a playbook graph in a phase document. After
adding, removing, or splitting a planned gate, run
`cafe playbook confirmation-gates <id>` and report that existing issue stop
contracts may need reconfirmation.

## 13. Acceptance Checklist

- [ ] The type and template order are correct.
- [ ] `name` equals the directory name, `description` states when to use it,
      and `version` is present.
- [ ] Only declared placeholders are used.
- [ ] Baton schema and common chat rules are not duplicated.
- [ ] References and scripts have explicit activation conditions.
- [ ] Shared rules are indexed in `cafe-workflow-common`.
- [ ] Plan producers use `output_artifact: plan`; consumers use
      `input_artifacts: [plan]` and read `{plan_file}`.
- [ ] Executable work uses the canonical Todo contract: one `## Todo List`,
      at most 100 rows, or the exact `No actionable work.` marker.
- [ ] A consumer uses one declared causal Todo projection and writes mutable
      progress only to its own `## Todo Progress` ledger.
- [ ] Every completed Todo row has bounded file, commit, and current targeted
      receipt evidence.
- [ ] Planned approval has both skill routing and playbook binding.
- [ ] Required tools cover every bound step without gratuitous permissions.
- [ ] Interruption-prone phases have a durable progress owner, bounded unit,
      dependency fingerprints, evidence-backed resume, and final sweep.

## 14. Plan-to-Execute Artifact Contract

The accepted `{plan_file}` is immutable input. The execution phase owns the
derived runtime checklist and its `{output_file}` `## Todo Progress` ledger;
the incoming plan remains immutable. The runtime checklist is a
procedure gate, not a cross-phase work ledger.

For a bridge that executes one plan and produces the next, distinguish the
incoming `{plan_file}` from the new `{output_file}`. A `not_required` result
must explain its skip reason and contain no open implementation tasks.

## 15. Forward-Only Plan Chains

When confirmed output determines the next phase's exact work, let the current
phase produce the next implementation plan after its own result is accepted.
Do not insert a checklist-copying phase. A later plan remains pending until
user confirmation, and downstream execution must not begin before that
confirmation.

## 16. Supporting Domain-Skill Selection

Evaluate each target CLI independently in this fixed order:

1. **CLI-native Skill**: record a suitable native skill and stop for that CLI.
2. **Open-source Skill**: only for unresolved CLIs, record source, license,
   revision, digest, maintenance, boundaries, and risks.
3. **Self-authored**: only after native and open-source options are unsuitable
   or explicitly rejected.

Prepare one proposed selection matrix covering every target CLI and obtain
explicit user confirmation before adopting, installing, vendoring, or writing
the selected skill. If one CLI advances, update the complete matrix and ask
again. Supporting skills provide domain procedure only; the phase skill owns
artifacts, checklist, approval, handoff, and CAFE acceptance.

## 17. Interruptible and Batch Phase Checkpoint/Resume Contract

A phase is interruption-prone when it handles multiple independent targets,
live APIs, subagents, long transformations, repeated review, or work that can
exceed one provider session. A simple atomic phase does not need a ledger.

### 17.1 Progress owner and lifecycle

`checklist.md` is the procedure gate for one iteration, not a per-target resume
ledger. If the output is exact-shape, public, or directly consumed by a hook,
use a declared/domain-owned separate ledger. Otherwise an output-owned
structured progress section is acceptable. Never create an undeclared hidden
sidecar. Keep the sole ledger until checklist, baton, handoff, and runtime
completion are durable; cleanup belongs to a post-success hook or retention
policy.

### 17.2 Minimum ledger

Record schema/version, run-context fingerprint, the complete stable target set,
per-target stage status, dependency fingerprints including dirty/untracked
content and mutable input versions, evidence for every completed stage, a final
sweep, and a finalized digest receipt. Record the digest algorithm,
scope/projection version, and scope. Do not hash a file that contains its own
digest field; use a canonical projection instead.

### 17.3 Bounded execution

Define a bounded unit and checkpoint after each stage with its evidence. The
default interruption budget is at most one target. Remote mutations require an
idempotent receipt; a request without read-back is not complete.

### 17.4 Resume algorithm

Recompute the target set and every dependency fingerprint before broad work.
Trust a completed stage only when the fingerprint and evidence integrity match.
Map changed inputs to affected stages only when the dependency graph proves the
impact; otherwise reopen all dependent rows. Resume from the first pending
stage, checkpoint every unit, and finalize only after the global sweep.

### 17.5 Migration of in-flight work

Initialize missing progress as pending, migrate only deterministic local
evidence, and leave review, approval, push, import, or publication pending
without an explicit receipt. Critical resume behavior belongs in the active
`SKILL.md`; changing a generated checklist is not a migration.

### 17.6 Source and activation verification

Validate strict skills and affected playbooks, confirm the resolved source, run
one preparation attempt for an existing iteration, verify the active installed
copy contains the new marker, and exercise at least four scenarios: one-unit
interruption, evidence-only legacy migration, exact/public output isolation,
and a fast phase without an unnecessary ledger.

## 18. Current Artifact Contract

This section is authoritative for current artifact normalization.

- Keep one canonical `## Todo List` with at most 100 rows. Plan rows use
  `PLAN-NNN`; correction rows use the declared source and prefix. Use exactly
  `No actionable work.` for intentional emptiness.
- Declare every backward correction route on the producing step with a
  destination-keyed route containing producer artifact, source kind, Todo
  source, and stable ID prefix. Resolve it from the persisted sender and
  destination edge, never from chat, summaries, artifact names, or session
  memory. The handoff must bind the exact artifact name, path, version, and
  content digest used by the consumer.
- Never infer correction mode from destination, artifact name, chat, or session
  memory.
- A custom artifact and source name uses the same declared path as builtin names;
  generic runtime must not infer behavior from names.
- Keep summary and workspace as distinct declared artifacts. A workspace
  companion is schema-versioned, atomically written, repository-bound, and
  verified with a canonical repository-relative nonsymlink `verification.json`
  receipt. Revalidate it immediately before agent and host-hook use.
- Preserve ordinary single-entry `artifact.json` behavior. Legacy v0.2 mixed
  `code` records remain readable only through their bounded compatibility
  adapter and cannot satisfy a current Git workspace requirement.
- A plan revision may retain an ID, unchanged retained work may not move to a
  new ID, and unrelated work may not reuse an existing ID. Progress belongs in
  the consumer output, not in the immutable plan.
- When a plan skill revises retained Work, its declared prior-plan input and
  sibling `artifact.json` supply the durable `todo_work_identities` map. The
  fingerprint is SHA-256 of `plan\x1f` followed by Work with internal whitespace
  collapsed to one space. Missing, malformed, or contradictory prior authority
  fails closed before the plan author runs.
- A `solution-alignment` plan owns no Todo authority. Its sibling
  `artifact.json` must carry `todo_identity_baseline` with schema version `1`
  and either an exact reference to the last detailed plan artifact or an
  explicit null artifact when no detailed plan exists. Carry that reference
  across repeated alignment rounds and resolve it before prompt preparation,
  cold takeover, and detailed-plan publication. Only a legacy version-1
  alignment artifact may migrate a missing baseline to explicit null.
- Validate every changed authoritative skill and playbook in strict mode and
  record targeted runtime evidence for all declared domain routes, custom names,
  process restart, and v0.2 compatibility.
- The metadata, HumanTask, confirmation, lifecycle, and artifact rules above
  are one authoritative contract; authors must preserve the complete contract
  when translating or reorganizing this reference.
