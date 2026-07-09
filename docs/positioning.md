# CAFE Positioning

> Confirmed strategic positioning document.

## Core Positioning

CAFE is a repo-first human-agent workflow system.

It helps technical founders, operators, and small agent-native teams keep workflow definitions, execution state, handoffs, decisions, and strategic documents in a versioned workspace. Agents can move automatable work forward, while human judgment, approval, and trusted host-side execution are separated into explicit workflow boundaries.

CAFE is not just a coding assistant or a general chat assistant. Its long-term direction is to help a company define, execute, track, and revise its operating workflows over time.

## Primary Users

- Technical founders and operators who need agents to help with long-running work, not only one-off tasks.
- Small agent-native teams that need workflow decisions, artifacts, and handoffs to remain reviewable and durable inside a repository.
- Engineers and workflow authors who define playbooks, skills, hooks, policies, and capability contracts.

CAFE can start with engineering-heavy users, but product decisions should preserve room for non-development workflows such as recruiting, onboarding, content production, operations, and internal SOPs.

## What CAFE Is Not

CAFE is not positioned as:

- An arbitrary shell or host-privilege executor for agents.
- An assistant that stores critical state only in chat context.
- A fixed-phase tool that only serves software development workflows.
- An immediate replacement for Notion, Jira, Linear, spreadsheets, or full management platforms.
- A no-code automation product where users can trigger external mutations without understanding the workflow model and authorization boundary.

These boundaries matter because CAFE should support stronger automation without turning workflow authoring into implicit host execution.

## Differentiation

CAFE's differentiation comes from five product commitments:

1. **Versioned workflow definitions**
   Playbooks, skills, templates, policies, SOPs, and strategic documents should live primarily in the repo so workflow changes can be reviewed, audited, and rolled back.

2. **Recoverable execution state**
   A workflow instance should have explicit state, artifacts, events, decisions, and handoffs instead of depending on a single chat session.

3. **Human-agent division of labor as a first-class concept**
   Work that an agent can perform, work that needs human judgment, and work that requires trusted host capability must be modeled separately instead of being collapsed into generic clarification.

4. **External mutation through capability contracts**
   Agents may declare the desired outcome, but the host-side capability layer decides whether and how to execute with trusted system permissions. Workflow artifacts should be declarative contracts, not shell privilege.

5. **Long-term orientation toward company workflows**
   The roadmap should move from a software-development workflow foundation toward human tasks, subflows, business objects, organizational memory, and governance only as those needs are validated.

## Product Boundaries

### Repo-first, not repo-only

CAFE uses the repository as the definition layer for:

- playbooks
- skills
- templates
- policies and SOPs
- versioned strategic documents

Runtime stores, task inboxes, analytics, dashboards, and business object stores do not need to stay bound to the repository interface forever. In the short and medium term, `.cafe/issues/` plus JSON, YAML, and Markdown are acceptable execution storage. Longer term, CAFE should preserve the option to move selected runtime state into a structured store.

### Agent authoring is not host trust

CAFE should allow agents to author workflows, skills, artifacts, contracts, and sandbox scripts.

An agent-authored script does not become trusted for host execution merely because it exists in the repository.

Host execution requires:

- a capability name or registry reference
- a manifest and argument schema
- policy checks
- risk classification
- human approval when required
- an auditable execution record

### Human tasks are not just clarification

When work requires a human to perform an action, make a judgment, approve a risk, or provide an external result, CAFE should move toward explicit `HumanTask`, `TaskResult`, and `WaitState` models instead of representing every human intervention as a one-off question.

## Roadmap Alignment

- `v0.2`
  Establish the generic workflow engine foundation: Skill, Playbook, Blackboard, Hook, GenericPhase, PlaybookRunner, and suspend/resume.

- `v0.2.x`
  Build the supporting surface around custom hooks, tooling, validation, simulation, dry runs, and an initial host-side capability contract prototype.

- `v0.3`
  Advance into human-agent workflow: HumanTask, trusted capability registry, host-executed script policy, and approval flow.

- `v0.4`
  Validate subflows and business object references so workflows can compose recursively.

- `v0.5`
  Evaluate organizational memory, governance, analytics, and operating-layer value.

## Capability Contract Positioning

Capability contracts exist primarily to protect the trusted host capability boundary.

Their secondary purpose is to reduce external mutation risk by making side effects explicit, reviewable, policy-checkable, and auditable.

When a design touches trusted capabilities, host execution, GitHub mutation, browser opening, deployment, credentials, network calls, or other external side effects, it should follow these boundaries:

- The agent layer produces intent, artifacts, and declarative contracts.
- The contract layer describes the capability, arguments, inputs, outputs, permissions, and side effects.
- The host capability layer decides whether execution is allowed according to registry, manifest, policy, and approval requirements.
- Arbitrary script paths are not execution authority.
- An agent-authored script is not automatically a trusted host-side script.
- External mutations should be auditable, limitable, deniable, and explainable to the user.

Runtime prompt assembly and resume-context fixes that only re-ground an agent on
the current workflow artifacts do not by themselves change this capability
contract positioning.

This positioning lets CAFE evolve toward stronger automation while preventing capability contracts from becoming a privilege-escalation mechanism.

## Decision Principles

When a feature or issue affects product positioning, ask:

1. Does this move CAFE toward a versioned, recoverable, auditable workflow system?
2. Does this preserve the distinction between agent work, human work, and trusted host capability?
3. Does this maintain the boundary between the agent layer, contract layer, and host capability layer?
4. Does this serve the next roadmap validation question instead of prematurely productizing a full platform?
5. Does this reduce the user's cognitive burden when understanding authorization and external side effects?

If the answer is no, the work should be narrowed, deferred, or preceded by a strategic document update.
