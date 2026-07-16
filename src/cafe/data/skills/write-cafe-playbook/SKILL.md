---
name: write-cafe-playbook
description: Use this skill when creating, restructuring, reviewing, or repairing a CAFE playbook YAML under src/cafe/data/playbooks or .cafe/playbooks. Covers step graphs, roles, artifacts, plan/checklist handoffs, forward plan chains, user review loops, conditional skips, hooks, tools, and strict validation. Use it whenever a user asks to write or update a CAFE playbook, or use-cafe-workflow identifies a playbook declarative defect.
version: 1.1.0
---

# Write CAFE Playbook

## Purpose
- Turn a confirmed workflow and its phase skills into a runtime-valid CAFE playbook.
- Make ownership, artifact flow, user gates, optional phases, and recovery paths explicit before execution.
- Own and repair the playbook declarative layer without crossing into phase, driver, or CAFE runtime implementation.

## Not This Skill
- Writing or restructuring a phase/shared/chat skill — use `write-cafe-phase` first.
- Running an existing workflow from the terminal — use `use-cafe-workflow`.
- Inventing missing phase behavior inside YAML; a playbook binds contracts but does not replace them.

## Required Reading
- Read `references/playbook-spec.md` before creating or structurally changing a playbook.
- Inspect `src/cafe/core/playbook.py` only when the schema or validator behavior may have changed since this reference was written.

## First Pass
- Locate every phase skill and read its `## Context`, `## Output`, user confirmation gates, routing rules, external-cost approvals, and completion conditions.
- Draw the intended happy path and identify optional phases, same-phase user revision loops, and exceptional recovery routes.
- Build an artifact matrix before writing YAML. Distinguish ordinary result/report handoffs from true plan → execute pairs.
- If one phase executes an incoming plan and its user-confirmed result determines the next phase, use a serial `plan` bridge instead of adding a checklist-only phase.
- Stop and fix the skill contracts with `write-cafe-phase` when a downstream phase would otherwise rediscover scope, guess source files, or implement work without a confirmed plan.

## Declarative Repair Boundary

- Accept a repair classification from `use-cafe-workflow` only when the evidence
  points to the playbook graph or declarations: steps, transitions, roles,
  artifacts, intents, allowed tools, hooks, prepare metadata, or confirmation
  gates.
- Edit the writable source of truth: `.cafe/playbooks/<id>.yaml` for a project
  playbook, or `src/cafe/data/playbooks/<id>.yaml` only when the current
  authorized repository is CAFE. Do not repair installed package contents,
  generated issue artifacts, or global skill copies.
- If the missing behavior belongs in a phase/shared/chat skill, return the
  classification to the driver for `write-cafe-phase`. If documented playbook
  declarations are valid but the CLI/runtime interprets them incorrectly,
  return a CAFE core-defect diagnosis instead of changing YAML to mask it.
- Do not edit driver/meta skills such as `use-cafe-workflow`, CAFE runtime
  Python, workflow state machinery, or host infrastructure. This skill is not a
  general CAFE self-modifier.
- Keep the repair minimal, rerun all validation and simulation commands below,
  and report whether the confirmation-gate set changed so the driver can
  reconfirm any stale issue stop contract.

## Design Rules
- Put builtin playbooks at `src/cafe/data/playbooks/<id>.yaml`; put project playbooks at `.cafe/playbooks/<id>.yaml`. Keep filename stem and `playbook.id` identical.
- Define only roles the steps actually use. Choose an existing agent and CLI that are available for that role.
- Give every step an explicit skill, role, artifact contract, allowed tools, hooks, valid intents, and complete `"on"` map.
- Use `output_artifact: plan` and downstream `input_artifacts: [plan]` whenever the upstream output is an implementation checklist. The execute skill must read `{plan_file}` and update the same checkboxes.
- A serial bridge may declare both `input_artifacts: [plan]` and `output_artifact: plan`; the incoming `{plan_file}` and next `{output_file}` are different files.
- Keep user-requested revisions in the phase responsible for the current output. Model them as self-loops through `confirm_output`, `need_clarification`, `need_permission`, or `manual_handoff`.
- Represent optional work with a confirmed or `not_required` plan and an explicit forward skip. Do not add routine backward cycles merely to rewrite a checklist.
- Reserve `allowed_goto` for deliberate conditional or exceptional routes. Keep the normal path in `"on"` so static simulation can explain it.
- Quote the YAML key `"on"`. Avoid custom status tokens; use CAFE's supported intents and mappings from the reference.
- For non-development workflows, explicitly decide `commands.prepare`; normally disable spec/plan setup prompts instead of inheriting irrelevant defaults.

## Writing Process
1. Inventory skills, expected outputs, user gates, paid operations, and source-of-truth files.
2. Write the happy-path step order and mark every optional phase and terminal step.
3. Create an artifact matrix with producer, artifact key, consumer, and whether the artifact is a result or implementation plan.
4. Define transitions for success, user review, clarification, permission, no-work skips, and exceptional goto paths.
5. Write the YAML using the template and field rules in `references/playbook-spec.md`.
6. Run `cafe skill validate --strict` so the referenced skills and placeholders are valid.
7. Run `cafe playbook validate <id> --strict`, inspect `cafe playbook show <id>`,
   and run `cafe playbook confirmation-gates <id>` to verify the planned user
   confirmation candidates.
8. Run `cafe playbook simulate <id> --dot`; fix unreachable steps, missing intent handlers, dead ends, and unintended directed cycles.
9. Add a focused schema or artifact assertion when using serial `plan` bridges or nontrivial skip branches.

## Delivery
- Report the playbook path, happy-path sequence, optional skip branches, plan/checklist pairs, and exceptional recovery routes.
- Report all validation and simulation results. Do not claim the playbook is runnable when referenced skills are missing or artifact bindings are incomplete.
- Include the exact invocation only after confirming how the issue is prepared and which playbook id the command must select.

## Final Review
- Every step is reachable from `entry_point`, and every terminal path reaches `_done` or an intentional user pause.
- Every declared `valid_intents` outcome has a matching transition key.
- User review loops stay in the current phase; normal workflow progress remains forward-only.
- Every implementation plan has exactly one producer and an execute consumer reading `plan`.
- Optional phases have an explicit skip and do not leave unchecked tasks in a `not_required` plan.
- `cafe playbook validate --strict` and `cafe playbook simulate --dot` both pass without unexplained findings.
