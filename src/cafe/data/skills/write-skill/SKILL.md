---
name: write-skill
description: Use this skill when creating or updating a CAFE skill under src/cafe/data/skills, especially when you need to define the skill scope, write SKILL.md, choose whether references/scripts are justified, and keep the skill aligned with CAFE workflow conventions.
version: 1.0.0
---

# Write Skill

## Purpose
- Create or update one CAFE skill that other agents can use reliably.
- Keep the skill concise, scoped, and consistent with CAFE's workflow model.

## First Pass
- Start from a real task or repeated correction, not generic advice.
- Identify the exact reusable behavior the skill should capture.
- Check nearby skills first so you do not duplicate an existing capability.
- If the new behavior belongs across multiple phases, prefer one shared/common skill instead of repeating the rule in each phase skill.

## Scope Rules
- Define one coherent unit of work.
- Prefer moderate detail: enough procedure to prevent drift, not exhaustive documentation.
- Favor procedures over declarations.
- Provide defaults, not menus.
- Only include instructions the agent would likely get wrong without this skill.

## CAFE Conventions
- Builtin CAFE skills live at `src/cafe/data/skills/<skill-name>/SKILL.md`.
- The folder name and frontmatter `name` must match exactly.
- Use lowercase letters, numbers, and hyphens only.
- `description` must say when to use the skill, not just what it contains.
- If the skill is part of workflow execution, assume runtime will provide file paths such as blackboard, artifacts, output file, checklist, and baton path.
- Do not duplicate global workflow handoff rules across many phase skills. Put those rules in a shared skill.
- Do not create extra docs like `README.md`, `CHANGELOG.md`, or design notes inside the skill folder.

## Writing Process
1. Write the frontmatter first.
2. Write a short title and purpose section.
3. Add only the always-needed workflow steps to `SKILL.md`.
4. If detailed material is only needed conditionally, move it to `references/` and say exactly when to read it.
5. If the task needs deterministic or repeated command execution, add a script under `scripts/` instead of embedding a long fragile command.
6. Add a concrete output template only when output shape matters.
7. Re-read the draft and cut anything that is obvious model knowledge or duplicated elsewhere.

## SKILL.md Checklist
- `name` matches the folder name.
- `description` starts from user intent and clearly says when to use the skill.
- The body stays focused on the core workflow.
- Steps are ordered and actionable.
- Defaults are explicit.
- Edge cases only appear if they materially change the workflow.
- References are one hop away from `SKILL.md`, not deeply chained.
- The skill does not rely on hidden context that runtime will not provide.

## When To Add References
- Add `references/` only for details that would otherwise bloat `SKILL.md`.
- Each reference file should be focused and named by topic.
- In `SKILL.md`, say exactly when to open each reference.

## When To Add Scripts
- Add `scripts/` when the same command or transformation would otherwise be rewritten repeatedly.
- Keep script inputs and outputs simple and explicit.
- Prefer structured output if the script will feed later agent steps.
- Make the script safe to rerun when possible.

## Output Expectations
- Produce the skill folder with `SKILL.md`.
- Add `references/` or `scripts/` only if they are justified by the workflow.
- If updating an existing skill, preserve the valid parts and only change the sections needed for the new behavior.

## Final Review
- Check that the skill is easy to trigger from its description alone.
- Check that the body is short enough to load without wasting context.
- Check that the skill composes cleanly with other CAFE skills.
- Check that shared rules are not copied into multiple phase skills.
