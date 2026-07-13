---
name: write-cafe-phase
description: Use this skill when creating or updating a CAFE workflow phase or its supporting shared/chat skill under src/cafe/data/skills or .cafe/skills. Covers phase scope, SKILL.md structure, placeholders, plan handoffs, and runtime conventions. Not for generic skill files, playbook YAML, or driver skills like use-cafe-workflow.
version: 2.2.0
---

# Write CAFE Phase Skill

## Purpose
- Create or update one CAFE **workflow** skill: a phase skill bound to a playbook step, a shared skill attached across phases, or a chat skill used inside `cafe chat`.
- Keep the skill concise, scoped, and consistent with CAFE's workflow model.

## Not This Skill
- Generic (non-CAFE) skill authoring for Claude Code or other CLIs — use your own skill-writing skill.
- Driver / meta skills that humans invoke from the terminal (e.g. `use-cafe-workflow`) — those follow §1/§3 of `references/skill-spec.md` but are not workflow skills.
- Creating or restructuring a CAFE playbook YAML — use `write-cafe-playbook` after the phase contracts are ready.

## Structural Spec (required reading)
- Before writing or restructuring any SKILL.md, read `references/skill-spec.md`.
- It defines the four skill types (phase / shared / chat / driver), the canonical section order per type, the runtime placeholder contract, and where handoff rules live.
- For a plan → execute phase pair, follow `references/skill-spec.md` §14 exactly; for a forward chain where one phase executes an incoming plan and produces the next phase's plan, also follow §15.
- If an existing skill conflicts with the spec, fix the skill to match the spec.

## First Pass
- Start from a real task or repeated correction, not generic advice.
- Identify the exact reusable behavior the skill should capture.
- Check nearby skills first so you do not duplicate an existing capability.
- If the new behavior belongs across multiple phases, prefer one shared/common skill instead of repeating the rule in each phase skill.
- If one phase decides and another phase implements, treat them as a plan → execute pair instead of inventing an ad hoc handoff file.
- If a phase's user-confirmed result determines the exact work of the next phase, let that phase end by producing the next confirmed plan; do not make the next phase rediscover scope.

## Scope Rules
- Define one coherent unit of work.
- Prefer moderate detail: enough procedure to prevent drift, not exhaustive documentation.
- Favor procedures over declarations.
- Provide defaults, not menus.
- Only include instructions the agent would likely get wrong without this skill.

## Plan → Execution Convention
- Follow the default playbook contract: the planning step uses `output_artifact: plan`; the execution step declares `input_artifacts: [plan]` and reads `Implementation Plan: {plan_file}` in `## Context`.
- The plan output itself is the implementation worklist. It must include a Test List and an ordered task breakdown using `- [ ]`; the execution phase marks those same items `- [x]` as work completes.
- Do not generate a separate plan-derived checklist sidecar. Runtime `checklist.md` is the phase-procedure checklist; plan task checkboxes are the cross-phase implementation checklist. Both may exist and serve different purposes.
- A domain-specific step or skill name is allowed, but the artifact key must remain exactly `plan` unless runtime placeholder support is deliberately extended.
- Forward chains may reuse the `plan` artifact key serially. A bridge step may declare both `input_artifacts: [plan]` and `output_artifact: plan`: `{plan_file}` is the incoming plan it executes, while `{output_file}` is the new plan for the next step. Never overwrite or repurpose the incoming plan.
- The bridge step completes and checks the incoming plan, obtains user acceptance of its result, then writes the next plan. If the next optional phase has no work, write a `not_required` plan with no unchecked implementation tasks and route around that phase.

## CAFE Conventions
- Builtin CAFE skills live at `src/cafe/data/skills/<skill-name>/SKILL.md`; project-level skills at `.cafe/skills/<skill-name>/SKILL.md`.
- The folder name and frontmatter `name` must match exactly.
- Workflow skill names carry the `cafe-` prefix in the folder name itself (`cafe-spec`, `cafe-workflow-common`); installs copy verbatim, no rename at copy time. See `references/skill-spec.md` §1/§3.
- `description` must say when to use the skill, not just what it contains.
- If the skill is part of workflow execution, assume runtime will provide file paths such as blackboard, artifacts, output file, checklist, and baton path.
- For a plan → execute pair, wire the playbook artifact contract before using `{plan_file}`; a skill body alone does not make the handoff work.
- Do not duplicate global workflow handoff rules across many phase skills. Put those rules in a shared skill.
- Do not create extra docs like `README.md`, `CHANGELOG.md`, or design notes inside the skill folder.

## Writing Process
1. Write the frontmatter first.
2. Write a short title and purpose section.
3. For a plan → execute pair or forward plan chain, define every `output_artifact: plan` → `input_artifacts: [plan]` binding and each implementation plan shape before writing the skills.
4. Add only the always-needed workflow steps to `SKILL.md`.
5. If detailed material is only needed conditionally, move it to `references/` and say exactly when to read it.
6. If the task needs deterministic or repeated command execution, add a script under `scripts/` instead of embedding a long fragile command.
7. If the task needs external network access, credentials, GitHub/API mutation, or other operations likely to be blocked by agent sandboxing, put the operation behind a skill script and document whether workflow hooks should call that script host-side.
8. Add a concrete output template only when output shape matters.
9. Re-read the draft and cut anything that is obvious model knowledge or duplicated elsewhere.

## SKILL.md Checklist
- `name` matches the folder name.
- `description` starts from user intent and clearly says when to use the skill.
- The body stays focused on the core workflow.
- Steps are ordered and actionable.
- Defaults are explicit.
- Edge cases only appear if they materially change the workflow.
- References are one hop away from `SKILL.md`, not deeply chained.
- The skill does not rely on hidden context that runtime will not provide.
- A plan → execute pair uses `plan` as the artifact key, the execute skill declares `{plan_file}` in `## Context`, and no sidecar duplicates the plan task list.
- A bridge phase that consumes one plan and produces the next clearly distinguishes incoming `{plan_file}` from next-plan `{output_file}`, completes the incoming checklist before handoff, and supports a `not_required` next plan.

## When To Add References
- Add `references/` only for details that would otherwise bloat `SKILL.md`.
- Each reference file should be focused and named by topic.
- In `SKILL.md`, say exactly when to open each reference.
- Put always-on workflow rules in `references/basic_principles.md` when they should become a checklist gate for every mode of that skill; keep mode-specific procedures in `references/execution_steps_*.md`.

## When To Add Scripts
- Add `scripts/` when the same command or transformation would otherwise be rewritten repeatedly.
- Add `scripts/` when the workflow must access external services, use credentials, push branches, create/update PRs, or mutate remote state; do not rely on the agent process doing those operations directly from inside its sandbox.
- Keep script inputs and outputs simple and explicit.
- Prefer structured output if the script will feed later agent steps.
- Make the script safe to rerun when possible.
- For scripts that publish external state, design them to be idempotent and suitable for host-side hook execution. The agent should prepare local artifacts; the script or hook should perform the external mutation and return a concise structured result.

## Output Expectations
- Produce the skill folder with `SKILL.md`.
- Add `references/` or `scripts/` only if they are justified by the workflow.
- If updating an existing skill, preserve the valid parts and only change the sections needed for the new behavior.
- If a plan → execute pair or forward plan chain is being wired into an existing playbook, update and validate the playbook in the same change. If no playbook exists yet, report every required `plan` artifact binding explicitly and do not claim the chain is connected.

## Final Review
- Check that the skill is easy to trigger from its description alone.
- Check that the body is short enough to load without wasting context.
- Check that the skill composes cleanly with other CAFE skills.
- Check that shared rules are not copied into multiple phase skills.
- Check that execution reads and updates the same implementation plan passed by `{plan_file}`, while runtime checklist rules remain in `execution_steps_*` and `basic_principles.md`.
- Check that user review loops stay in the phase responsible for the output; do not add routine backward transitions merely to regenerate a checklist. Reopen upstream only when a previously confirmed source of truth is invalidated.
