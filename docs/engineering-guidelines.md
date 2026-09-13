# Engineering Guidelines

This document contains confirmed repository-wide engineering and architecture
constraints for CAFE. Workflow specification, planning, implementation, and
review must treat these constraints as authoritative technical guidance.

## Generic workflow architecture

Default workflow behavior is owned by playbook YAML, skills, and the
mode-neutral workflow runtime. Generic workflow services should expose reusable
contracts without embedding the identity, policy, or authorization model of a
particular caller.

## Driver dependency boundary

Driver policy, mode, session, prompt, and authorization semantics are owned by
`src/cafe/driver/` and `src/cafe/data/skills/use-cafe-workflow/`. Dependencies
flow from those Driver-owned adapters toward mode-neutral workflow services,
never from generic workflow layers back into Driver code.

Code under `src/cafe/core/`, `src/cafe/phases/`, generic UI commands, generic
HumanTask services and records, hooks, and agents must remain Driver-free. They
must not import `cafe.driver` or encode Driver-specific flags, actor names,
contracts, routing, or authorization rules. When Driver needs a generic
operation, expose a mode-neutral interface at the owning layer and adapt it only
from a Driver-owned boundary.

## Boundary changes

Architecture-boundary tests are protected contracts. Do not weaken them, add a
feature-specific allowlist exception, or relocate specialized semantics merely
to make a feature pass. A real boundary change requires explicit user approval
and an accompanying update to the confirmed strategic or engineering guidance.
