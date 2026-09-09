---
name: write-cafe-agent
description: Use this skill when creating, updating, reviewing, or repairing a CAFE agent role file under src/cafe/data/agents, ~/.cafe/agents, or .cafe/agents. Covers agent identity, role boundaries, native language, and flat behavioral guidelines that become phase checklist gates. Not for workflow phase procedures or playbook routing.
version: 1.0.0
---

# Write CAFE Agent

## Purpose
- Create or update one CAFE agent role without moving phase procedure into the persona.
- Preserve the contract between agent guidelines and phase checklists.
- Keep equivalent language variants behaviorally aligned.

## Structural Spec (required reading)
- Before writing or restructuring an agent file, read `references/agent-spec.md` completely.
- Use the PM, developer, and reviewer examples in that reference as the canonical shape.
- If an existing agent conflicts with the spec, repair the file without broadening its intended role.

## Source Boundary
- Edit `.cafe/agents/<role>/<name>.md` for a project-owned agent.
- Edit `~/.cafe/agents/<role>/<name>.md` only when the user explicitly requests a global personal agent.
- Edit `src/cafe/data/agents/<role>/<name>.md` only when the authorized repository is CAFE and the agent is built in.
- Do not edit runtime copies under issue workspaces or generated prompts.

## Workflow
1. Identify the role, intended language, and phases that use the agent.
2. Inspect the corresponding PM, developer, or reviewer built-in agent and any same-role language counterpart.
3. Write valid frontmatter, one concise role statement, and a flat list of actionable behavioral guidelines according to `references/agent-spec.md`.
4. Keep phase procedure, artifact handling, handoff routing, tools, and repository-wide invariants in the owning phase/shared skill rather than the agent file.
5. Preview the extracted checklist and verify that every guideline is meaningful in every phase where this agent may run.
6. Run the focused agent, checklist-composer, and catalog tests that cover the changed role.

## Checklist Coupling
- CAFE extracts every line whose trimmed form starts with `- ` and converts it into an `## Agent Guidelines Checklist` item when the phase opts into role guidance.
- Treat every top-level bullet as an executable gate: make it independently actionable, role-specific, and valid across the agent's phases.
- Do not use nested bullets, decorative lists, examples, or metadata bullets; the current extractor also treats indented `- ` lines as checklist items.
- Do not rely on paragraphs alone for behavioral rules because paragraphs do not become checklist items.

## Validation
- Confirm the filename stem and frontmatter `name` match exactly.
- Confirm `description` identifies the role and declares the native language when it is not English.
- Confirm the body has one role statement followed by at least one flat `- ` guideline.
- Confirm guideline extraction produces only the intended checklist gates.
- For language counterparts, compare the resulting behaviors rather than requiring literal translation.

## Output Expectations
- Produce the requested agent file and no unrelated role or phase changes.
- Report which phase checklists consume its guidelines.
- If the requested behavior belongs to a phase or playbook, stop and route that part to `write-cafe-phase` or `write-cafe-playbook` instead of encoding it in the persona.
