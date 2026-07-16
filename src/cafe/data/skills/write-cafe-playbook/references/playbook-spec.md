# CAFE Playbook Authoring Reference

Use this reference while designing or validating a CAFE playbook. The runtime schema remains authoritative in `src/cafe/core/playbook.py`.

## 1. Location And Identity

| Scope | Path | Use |
| --- | --- | --- |
| Builtin | `src/cafe/data/playbooks/<id>.yaml` | Shipped by CAFE for general reuse |
| Project | `.cafe/playbooks/<id>.yaml` | Domain/project-specific workflow committed with its custom skills |

- Keep `<id>`, filename stem, and `playbook.id` identical so strict validation does not report structural drift.
- A project playbook overrides a builtin playbook with the same id.
- Custom phase skills belong in `.cafe/skills/`; builtin phase skills belong in `src/cafe/data/skills/`.

## 2. Minimal Shape

```yaml
playbook:
  id: example
  name: "Example Workflow"

roles:
  developer:
    description: "Workflow operator"
    default_agent: "David"
    default_cli: "claude"

steps:
  plan_work:
    type: skill
    skill: cafe-domain_plan
    role: developer
    assignee_type: agent
    input_artifacts: []
    output_artifact: plan
    valid_intents: [confirmed, ready_for_review, need_clarification, needs_changes]
    allowed_tools: [Read, Edit, Write, Grep, Glob]
    hooks:
      prepare_input: [UserInputCollector]
    "on":
      await_agent: execute_work
      confirm_output: plan_work
      need_clarification: plan_work
      manual_handoff: plan_work

  execute_work:
    type: skill
    skill: cafe-domain_execute
    role: developer
    assignee_type: agent
    input_artifacts: [plan]
    output_artifact: domain_result
    valid_intents: [confirmed, ready_for_review, need_clarification, need_permission, needs_changes]
    allowed_tools: [Read, Edit, Write, Grep, Glob, Bash]
    hooks:
      prepare_input: [UserInputCollector]
      after_execute: [PermissionRetryHandler]
    "on":
      await_agent: _done
      confirm_output: execute_work
      need_clarification: execute_work
      need_permission: execute_work
      manual_handoff: execute_work

entry_point: plan_work
```

## 3. Step Fields

| Field | Rule |
| --- | --- |
| `type` | Usually `skill`; use `subflow` only when an actual subflow exists |
| `skill` | Existing resolved skill name, or an iteration mapping with numbered keys/default |
| `role` / `chat_role` | Must exist in top-level `roles` |
| `assignee_type` | Use `agent`; other values are currently reserved and warn |
| `input_artifacts` | Artifact keys already produced by earlier or conditional paths |
| `output_artifact` | The key registered when `{output_file}` exists |
| `valid_intents` | Supported `PhaseStatusCode` tokens the phase may return |
| `allowed_tools` | Least broad set that still allows the skill to complete |
| `hooks` | Runtime-supported prepare/execute/publish hooks only |
| `allowed_goto` | Explicit non-default routes; do not use as the happy path |
| `"on"` | Complete intent-key → step transition map |

Quote `"on"`; unquoted YAML 1.1 may parse it as a boolean before normalization.

## 4. Outcome To Transition Mapping

`valid_intents` contains outcome tokens. The `"on"` map uses transition keys.

| Outcome token | `"on"` key | Typical target |
| --- | --- | --- |
| `confirmed`, `await_agent` | `await_agent` | Next normal step |
| `ready_for_review`, `confirm_output` | `confirm_output` | Current step, pausing for user review |
| `need_clarification` | `need_clarification` | Current step |
| `need_permission` | `need_permission` | Current step |
| `needs_changes`, `rejected`, `skip_review` | `manual_handoff` | Current step or an allowed exceptional target |
| `no_changes_needed` | `no_changes_needed` | Forward skip target |
| `workflow_complete` | `workflow_complete` | `_done` |

Every declared intent must have a resolvable key. `cafe playbook simulate` reports missing handlers.

## 5. User Gates And Loops

`on.confirm_output` is the first-class declaration of a planned kickoff
confirmation gate. The workflow driver derives the user's stop-contract
candidates from steps that declare this key. Use it only when the user can
meaningfully approve the completed output before the normal path continues.
Reactive `need_clarification`, `need_permission`, and `alignment_checkpoint`
pauses are safety interruptions, not scheduled confirmation candidates.

Use a self-loop when the user is reviewing the current phase's output:

```yaml
"on":
  await_agent: next_step
  confirm_output: current_step
  need_clarification: current_step
  need_permission: current_step
  manual_handoff: current_step
```

- Add `UserInputCollector` so resumed input reaches the same phase.
- Add `PermissionRetryHandler` when local/external execution may be permission-gated.
- Keep preview revisions in execute phases and scope/solution revisions in plan phases.
- Use a backward route only when a previously confirmed source of truth is invalidated, not for ordinary tuning.

## 6. Artifact Matrix

Build this table before writing YAML:

| Producer | Output key | Artifact meaning | Consumer | Consumer input |
| --- | --- | --- | --- | --- |
| `domain_plan` | `plan` | Confirmed implementation checklist | `domain_execute` | `[plan]` |
| `domain_execute` | `domain_result` | Execution report/result paths | `next_analysis` | `[domain_result]` when useful |

Use `plan` only when the artifact itself contains the executable worklist, including Test List, Definition of Done, stable IDs, and `- [ ]` tasks. Reports and manifests should use domain result keys.

### Serial Plan Bridge

```yaml
  discover_and_plan:
    output_artifact: plan

  execute_and_plan_next:
    input_artifacts: [plan]
    output_artifact: plan

  execute_next:
    input_artifacts: [plan]
    output_artifact: next_result
```

The bridge receives the old plan as `{plan_file}` and writes the next plan to `{output_file}`. Runtime resolves the incoming artifact before registering the new one, so these are separate versioned files. The bridge must:

1. Complete and check the incoming plan.
2. Obtain user acceptance of its result.
3. Produce and confirm the next plan, or produce `not_required` with no unchecked tasks.

Do not duplicate plan tasks in a sidecar checklist. Runtime `checklist.md` is procedural; the plan checkboxes are the cross-phase implementation worklist.

## 7. Optional Phases And Skips

Prefer an explicit forward skip:

```yaml
  analyze:
    output_artifact: plan
    valid_intents: [confirmed, no_changes_needed]
    allowed_goto: [optional_execute, next_required_step]
    "on":
      await_agent: optional_execute
      no_changes_needed: next_required_step
```

- The no-work plan should be `not_required`, state the reason, and contain no unchecked tasks.
- In interactive mode, an explicit skill-written baton may bypass a redundant confirmation after the user already approved the skip.
- Ensure every skip target has enough source information to continue without an artifact that only the skipped phase would have produced.

## 8. Tools, Hooks, And Prepare

- Use standard tool names such as `Read`, `Edit`, `Write`, `Grep`, `Glob`, `Bash`, `WebFetch`, and `WebSearch`. Scope Bash when a narrower command contract is practical.
- `UserInputCollector` belongs on phases that pause and resume with user feedback.
- `PermissionRetryHandler` belongs on execution phases that may hit permission boundaries.
- Use specialized hooks only when their implementation exists and their lifecycle stage is valid.
- Non-software playbooks should normally set `commands.prepare.prompt_for_spec_plan_config: false`; otherwise they inherit development-oriented preparation prompts.
- Declarative prepare fields support only the write targets defined in `src/cafe/core/prepare_fields.py`. Do not invent domain write targets in YAML.

## 9. Validation Sequence

Run all applicable checks from the target repo:

```bash
cafe skill validate --strict
cafe playbook validate <id> --strict
cafe playbook show <id>
cafe playbook simulate <id> --dot
```

The simulation should report:

- no unreachable steps;
- no missing intent handlers;
- no dead-end steps;
- no unexplained directed cycles beyond intentional self-loops.

For a serial plan bridge, also assert the contract directly:

```python
assert steps["bridge"]["input_artifacts"] == ["plan"]
assert steps["bridge"]["output_artifact"] == "plan"
```

## 10. Acceptance Checklist

- [ ] Filename stem equals `playbook.id`.
- [ ] Every skill resolves and passes strict skill validation.
- [ ] Every role and `chat_role` is declared.
- [ ] Every step is reachable from `entry_point`.
- [ ] Every declared intent has an `"on"` handler.
- [ ] Every normal path reaches `_done` or an intentional user pause.
- [ ] Plan producers output `plan`; execute consumers input `plan` and read `{plan_file}`.
- [ ] Serial bridges distinguish incoming `{plan_file}` from next `{output_file}`.
- [ ] Optional phases have a safe forward skip and a `not_required` contract.
- [ ] User review loops remain in the phase that owns the current output.
- [ ] Tools and hooks are sufficient but not gratuitously broad.
- [ ] Strict validation, show, and simulation results are reported.
