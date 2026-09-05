---
name: write-cafe-phase
description: Use this skill when creating, updating, or repairing a CAFE workflow phase or its supporting shared/chat skill under src/cafe/data/skills or .cafe/skills. Covers phase scope, SKILL.md structure, placeholders, plan handoffs, interruption-safe checkpoint/resume behavior, and runtime conventions, including declarative defects identified by use-cafe-workflow. Not for generic skill files, playbook YAML, driver skills, or CAFE core/runtime defects.
version: 2.10.0
---

# Write CAFE Phase Skill

## Purpose
- Create or update one CAFE **workflow** skill: a phase skill bound to a playbook step, a shared skill attached across phases, or a chat skill used inside `cafe chat`.
- Keep the skill concise, scoped, and consistent with CAFE's workflow model.
- Own and repair the phase/shared/chat declarative layer without crossing into driver or CAFE runtime implementation.

## Not This Skill
- Generic (non-CAFE) skill authoring for agent CLIs — use your own skill-writing skill.
- Driver / meta skills that humans invoke from the terminal (e.g. `use-cafe-workflow`) — those follow §1/§3 of `references/skill-spec.md` but are not workflow skills.
- Creating or restructuring a CAFE playbook YAML — use `write-cafe-playbook` after the phase contracts are ready.

## Structural Spec (required reading)
- Before writing or restructuring any SKILL.md, read `references/skill-spec.md`.
- It defines the four skill types (phase / shared / chat / driver), the canonical section order per type, the runtime placeholder contract, and where handoff rules live.
- For a plan → execute phase pair, follow `references/skill-spec.md` §14 exactly; for a forward chain where one phase executes an incoming plan and produces the next phase's plan, also follow §15.
- For a phase that processes many independent items, performs long external/API work, runs repeated reviews, or may exceed one provider session, follow `references/skill-spec.md` §17 exactly.
- If an existing skill conflicts with the spec, fix the skill to match the spec.

## First Pass
- Start from a real task or repeated correction, not generic advice.
- Identify the exact reusable behavior the skill should capture.
- Check nearby skills first so you do not duplicate an existing capability.
- If the new behavior belongs across multiple phases, prefer one shared/common skill instead of repeating the rule in each phase skill.
- If one phase decides and another phase implements, treat them as a plan → execute pair instead of inventing an ad hoc handoff file.
- If a phase's user-confirmed result determines the exact work of the next phase, let that phase end by producing the next confirmed plan; do not make the next phase rediscover scope.
- Identify interruption boundaries before writing a batch or long-running phase. Define how much work may be lost, where durable progress lives, and how a retry proves which work is safe to skip.
- If the phase needs a reusable domain procedure, follow `references/skill-spec.md` §16 before writing it from scratch.

## Supporting Skill Selection

- Treat domain behavior such as UI design, specification, or code review as phase-skill composition, not as a new CAFE core capability.
- Evaluate candidates independently for every supported target CLI in this order: (1) a suitable CLI-native skill, (2) a suitable auditable open-source skill, then (3) a newly authored procedure. Stop at the first suitable tier for that CLI; unresolved CLIs may continue to lower tiers. Never skip an available tier without explaining why it is unsuitable.
- Build one proposed selection matrix covering every target CLI. Before adopting any candidate, installing or vendoring its content, or starting a self-authored option, present the matrix, source and license when applicable, material tradeoffs, and integration plan to the user; wait for explicit confirmation.
- If the user rejects one CLI's proposed candidate, advance only that CLI to its next tier, rebuild the matrix, and ask again. Never propose the self-authored option for a CLI until its native and open-source tiers have both been evaluated and ruled out or rejected.
- Read-only discovery and evaluation for unresolved CLI rows may happen before approval; confirmation is required before the proposed matrix becomes the selected implementation.
- Keep the CAFE phase skill authoritative for its workflow contract, artifacts, checklist, approval gates, and handoff. A selected supporting skill supplies domain procedure only.
- Resolve and package the confirmed choice at authoring time. Do not make workflow execution search the network, download mutable content, or silently substitute a different skill.

## Declarative Repair Boundary

- Accept a repair classification from `use-cafe-workflow` only when the evidence
  points to a phase/shared/chat skill contract, placeholder, routing rule,
  reference, asset, or skill-owned supporting resource.
- Edit the writable source of truth: `.cafe/skills/<skill-name>/` for a project
  skill, or `src/cafe/data/skills/<skill-name>/` only when the current authorized
  repository is CAFE. Do not repair installed package contents, runtime-created
  prompts, generated issue artifacts, or global CLI skill copies.
- If the defect is a playbook graph or binding error, return the classification
  to the driver for `write-cafe-playbook`. If the declared skill contract is
  valid but CAFE injects, activates, or executes it incorrectly, return a CAFE
  core-defect diagnosis rather than changing the skill to mask runtime behavior.
- Do not edit driver/meta skills such as `use-cafe-workflow`, CAFE runtime
  Python, workflow state machinery, or host infrastructure. This skill is not a
  general CAFE self-modifier.
- Keep the repair minimal and rerun the affected skill and playbook validation.
  If routing or planned confirmation behavior changes, report the new gate set
  so the driver can reconfirm any stale issue stop contract.

## Planned User Confirmation Gates
- When a phase output needs planned user approval, the phase skill must route the output to `user` and the bound playbook step must declare `on.confirm_output`. Neither side alone is a complete contract.
- Treat the active playbook's `on.confirm_output` declarations as the source of planned confirmation gates. A matching binding with `feedback_delivery` is a mandatory user-owned HumanTask; all other matches are kickoff stop-contract candidates. Do not hardcode `user_required` or `driver_confirmable` policy inside a phase skill.
- Keep `need_clarification`, `need_permission`, and `alignment_checkpoint` as reactive safety interruptions; they are not scheduled confirmation candidates.
- The stop contract is step-level. If one phase contains multiple approval moments that must allow different user/driver ownership, split them into separate playbook steps instead of inventing pseudo-step gate names.
- After adding or removing a planned gate, run `cafe playbook confirmation-gates <id>`, report the changed candidate set, and require the workflow driver to reconfirm any stale issue contract before the next `cafe make`.

## Same-Phase Staged Checkpoint

- Keep a prerequisite decision and the completed output in one phase only when they share ownership, artifact lifecycle, and final approval. Use a mandatory user-owned reactive checkpoint for the prerequisite decision; the completed output still uses the step's planned `confirm_output` gate.
- Persist durable, unambiguous stage evidence in a phase-owned artifact. Resume from that evidence rather than inferring stage from the iteration number, prose, or session memory, and fail closed when the evidence or required answer is absent or ambiguous.
- Keep the provisional output bounded, clearly unconfirmed, and unable to reach downstream execution. Its HumanTask prompt must be self-contained and expose the material scope and tradeoffs needed for the decision.
- Treat iteration selectors as first-entry/resume routing only. A stage may span multiple iterations, so name checklist references by procedural purpose unless the procedure itself is inherently tied to a particular iteration.
- Split the work into separate playbook steps when stages need different ownership, independent artifacts or reuse, separately configurable planned gates, or different downstream reachability.

## Scope Rules
- Define one coherent unit of work.
- Prefer moderate detail: enough procedure to prevent drift, not exhaustive documentation.
- Favor procedures over declarations.
- Provide defaults, not menus.
- Only include instructions the agent would likely get wrong without this skill.

## Interruptible and Batch Phases

- Treat a phase as interruption-prone when it processes multiple independent targets, depends on live APIs or subagents, performs repeated review loops, or can reasonably outlast one CLI/provider session.
- Give interruption-prone phases a durable progress contract. Runtime `checklist.md` records whether the current phase procedure is complete; it is not a per-target resume ledger.
- Choose the progress owner only after inspecting the phase output template, downstream consumers, finalizers, and publish hooks. Embed progress in `{output_file}` only when those contracts explicitly permit partial and final ledger content and the evidence is sanitized; otherwise use a separately declared artifact or domain-owned workspace ledger with an explicit safe-finalization contract.
- Record a run-context fingerprint, the complete stable target set, per-target stages, per-target/stage dependency fingerprints (including relevant dirty or mutable content), sanitized evidence, and global-finalization state. Checkpoint immediately after each bounded unit so an abrupt provider failure loses at most the unit currently running.
- On retry, trust `done` only when that stage's dependency fingerprint still matches and its evidence exists. Invalidate only rows whose impact can be mapped deterministically; when impact is ambiguous, invalidate every stage that depends on the changed input instead of assuming unrelated completion.
- Finalize without deleting the only resume ledger. Record `finalized` plus a versioned digest receipt: a separate ledger hashes the complete final-artifact bytes, while an embedded ledger hashes a canonical domain-payload projection that excludes ledger/finalization metadata. Record the algorithm and scope/projection version, verify that exact scope on resume, and retain the owner through durable checklist, baton, handoff, and runtime completion. Cleanup belongs to a post-success runtime/host hook or later retention policy, never to the phase agent before durable completion is observable.
- When adding resumability to an in-flight legacy iteration, initialize the ledger and migrate only deterministic local evidence. Never infer a subjective review, human approval, or remote mutation as complete without its explicit receipt.
- Put the critical resume algorithm in `SKILL.md`, even when new checklist gates are also added to `references/execution_steps_*`. Phase preparation refreshes an existing iteration's derived `checklist.md` from the current resolved skill: only exactly unchanged completed items remain complete, while new or changed gates reopen. References can therefore add current checklist gates, but they never replace the always-on resume algorithm in `SKILL.md`.
- Never repair an in-flight issue by manually editing generated `output.md`, `checklist.md`, or CLI-native installed copies. The phase agent owns its output; the runtime owns generated state and reinstalls the resolved source skill.
- After an execution attempt reaches phase preparation, verify the worktree-local CLI-native copy contains a unique marker from the new source. This checks activation without treating the installed copy as source of truth.
- Provider retry scheduling remains driver/runtime behavior. The phase contract only makes retries safe and progressive; do not add an internal infinite retry loop to mask provider limits.

## Plan → Execution Convention
- Follow the standard playbook contract: the planning step uses `output_artifact: plan`; the execution step declares `input_artifacts: [plan]` and reads `Implementation Plan: {plan_file}` in `## Context`.
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
- For planned output approval, wire `on.confirm_output` in the playbook before claiming the phase participates in confirmation handling; routing text in the skill alone is insufficient.
- Every phase skill declares a provider-neutral `workflow.execution_profile` with workload, reasoning, risk domains, and fallback strength. Never put a CLI provider, model name, pricing tier, or current availability claim in that profile.
- If one playbook step selects different skills by iteration, describe each skill honestly. The workflow driver resolves the actual iteration skill and conservatively aggregates all variants at kickoff.
- Declare every mandatory tool dependency once in `workflow.required_tools`; every playbook step that selects the skill must grant it in `allowed_tools`.
- Do not duplicate global workflow handoff rules across many phase skills. Put those rules in a shared skill.
- Do not create extra docs like `README.md`, `CHANGELOG.md`, or design notes inside the skill folder.

## Writing Process
1. Write the frontmatter first, including `workflow.execution_profile` for every phase skill.
2. Write a short title and purpose section.
3. If reusable domain guidance is needed, complete the supporting-skill evaluation and user-confirmed selection in §16 before implementing that guidance.
4. For a plan → execute pair or forward plan chain, define every `output_artifact: plan` → `input_artifacts: [plan]` binding and each implementation plan shape before writing the skills.
5. For every planned user approval, add the phase routing decision and verify the bound playbook step declares `on.confirm_output`.
6. For an interruption-prone phase, define the §17 checkpoint schema, bounded work unit, resume validation, and legacy migration path before writing the detailed procedure.
7. Add only the always-needed workflow steps to `SKILL.md`; resumability needed by an in-flight iteration is always-needed, not a checklist-reference-only detail.
8. If detailed material is only needed conditionally, move it to `references/` and say exactly when to read it.
9. If the task needs deterministic or repeated command execution, add a script under `scripts/` instead of embedding a long fragile command.
10. If the task needs external network access, credentials, GitHub/API mutation, or other operations likely to be blocked by agent sandboxing, put the operation behind a skill script and document whether workflow hooks should call that script host-side.
11. Add a concrete output template only when output shape matters.
12. Re-read the draft and cut anything that is obvious model knowledge or duplicated elsewhere.

## SKILL.md Checklist
- `name` matches the folder name.
- `description` starts from user intent and clearly says when to use the skill.
- A phase skill has a provider-neutral `workflow.execution_profile`; it contains no CLI or model names.
- The body stays focused on the core workflow.
- Steps are ordered and actionable.
- Defaults are explicit.
- Edge cases only appear if they materially change the workflow.
- References are one hop away from `SKILL.md`, not deeply chained.
- The skill does not rely on hidden context that runtime will not provide.
- A plan → execute pair uses `plan` as the artifact key, the execute skill declares `{plan_file}` in `## Context`, and no sidecar duplicates the plan task list.
- A bridge phase that consumes one plan and produces the next clearly distinguishes incoming `{plan_file}` from next-plan `{output_file}`, completes the incoming checklist before handoff, and supports a `not_required` next plan.
- Every planned output-confirmation route has a matching playbook `on.confirm_output` declaration and is classified as assignable or mandatory; reactive user interruptions are not mislabeled as kickoff candidates.
- A same-phase staged checkpoint, when used, is mandatory user-owned, resumes from durable stage evidence, remains unreachable from downstream execution until final `confirm_output`, and is not presented as a kickoff-assignable approval.
- Mandatory tools are declared in `workflow.required_tools`; optional diagnostics are not made unconditional, and every binding playbook grants the declared tools.
- An interruption-prone phase has an output-compatible durable progress owner, stable target identity, per-target/stage dependency fingerprints, bounded checkpoint unit, evidence-backed resume algorithm, final global sweep, non-self-referential versioned finalization digest, and post-success ledger retention/cleanup contract; it does not use runtime checklist state as per-target progress.
- A repair intended to protect an existing iteration puts the critical rule in `SKILL.md`, relies on phase preparation to refresh the derived `checklist.md` while retaining only exactly unchanged completion, and defines evidence-only migration for work produced before the ledger existed.
- Reusable domain procedure follows native → open-source → self-authored evaluation independently per target CLI, and the complete selection matrix has explicit user confirmation before adoption or implementation.

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
- If planned confirmation behavior changes in an existing playbook, update it in the same change, run `cafe playbook confirmation-gates <id>`, and report that existing issue stop contracts may be stale.

## Final Review
- Check that the skill is easy to trigger from its description alone.
- Check that the body is short enough to load without wasting context.
- Check that the skill composes cleanly with other CAFE skills.
- Check that shared rules are not copied into multiple phase skills.
- Check that execution reads and updates the same implementation plan passed by `{plan_file}`, while runtime checklist rules remain in `execution_steps_*` and `basic_principles.md`.
- Check that a long batch cannot lose or repeat more than one declared bounded unit after an abrupt provider interruption, and that completed work remains independently auditable.
- For an in-flight repair, check the resolved source with `cafe skill show`/`list`, then after phase preparation inspect the worktree-local CLI-native copy for the new marker; never edit that copy directly.
- Check that user review loops stay in the phase responsible for the output; do not add routine backward transitions merely to regenerate a checklist. Reopen upstream only when a previously confirmed source of truth is invalidated.
- Check that every planned user approval is visible in the assignable or mandatory section of `cafe playbook confirmation-gates <id>` and that distinct approval ownership choices are represented by distinct playbook steps.
- Check that supporting domain guidance was selected at authoring time with user confirmation, remains subordinate to the phase contract, and introduces no runtime network discovery or silent fallback.
