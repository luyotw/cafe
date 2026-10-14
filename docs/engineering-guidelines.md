# Engineering Guidelines

This document contains confirmed repository-wide engineering and architecture
constraints for CAFE. Workflow specification, planning, implementation, and
review must treat these constraints as authoritative technical guidance.

## Capabilities must not be gated by release versions

No CAFE feature may require, allowlist, denylist, or otherwise bind to particular
CAFE, provider, CLI, SDK, or dependency release versions without a documented,
evidence-backed reason. This applies to exact matches, minimum/maximum versions,
major-version ranges, and requirements that a cache's producer version match the
installed client. An upgrade, an unfamiliar version, or missing version metadata
must not by itself disable a feature or discard otherwise valid data.

Determine compatibility from the required capabilities, command options,
protocol/data structure, identity, and accounting semantics. Validate the
evidence actually needed by the feature. Release versions may be recorded for
diagnostics and reproducibility; a tested version is evidence about that test,
not a list of the only supported versions. Missing or invalid capability evidence
may still prevent execution, but the diagnostic must identify that actual gap.

A version gate is an exception only when there is a concrete reason, such as a
reproduced upstream bug or a demonstrated incompatible API/protocol change that
cannot be handled adequately through capability or data validation. Every
exception must document:

- The failure, with a reproducible case or a specific upstream issue/change as
  evidence. "Only this version was tested" is not sufficient.
- The affected feature and narrowest affected version range, why direct
  capability/data checks are insufficient, and the available recovery path.
- The condition for removing the gate and regression tests covering both the
  affected case and compatible versions outside that range.

Specification, planning, implementation, and review must enforce this rule.
Review must reject unexplained version gates, including attempts to restore
them through cache validation or provider adapters. Explicit protocol/schema
validation, release numbering, and dependency locks for reproducible builds are
separate concerns; they do not justify a feature gate based on release identity.

## Generic workflow architecture

Default workflow behavior is owned by playbook YAML, skills, and the
mode-neutral workflow runtime. Generic workflow services should expose reusable
contracts without embedding the identity, policy, or authorization model of a
particular caller.

## Manager dependency boundary

Manager policy, mode, session, prompt, and authorization semantics are owned by
`src/cafe/manager/` and `src/cafe/data/skills/use-cafe-workflow/`. Dependencies
flow from those Manager-owned adapters toward mode-neutral workflow services,
never from generic workflow layers back into Manager code.

Code under `src/cafe/core/`, `src/cafe/phases/`, generic UI commands, generic
HumanTask services and records, hooks, and agents must remain Manager-free. They
must not import `cafe.manager` or the legacy `cafe.driver` compatibility package,
or encode Manager-specific flags, actor names, contracts, routing, or authorization
rules. When Manager needs a generic
operation, expose a mode-neutral interface at the owning layer and adapt it only
from a Manager-owned boundary.

## Boundary changes

Architecture-boundary tests are protected contracts. Do not weaken them, add a
feature-specific allowlist exception, or relocate specialized semantics merely
to make a feature pass. A real boundary change requires explicit user approval
and an accompanying update to the confirmed strategic or engineering guidance.

## Keep generic runtime independent of workflow topology

Generic core and runtime code must not branch on built-in phase, role, artifact,
or playbook names. Relationships such as producer ownership, artifact selection,
source classification, and causal routing belong in declarative skill or
playbook metadata and must be resolved through that metadata at runtime.

Closed enums or lookup tables containing names such as `plan`, `develop`,
`review`, `qa`, or `pr` are acceptable only when those values define an explicit
public domain contract rather than workflow topology. A reusable declaration
must support an equivalent custom phase without requiring changes to core code.
Todo identity presentation, including an ID prefix, follows the same rule: it is
declared by the producer route and is never inferred from a built-in source name.

Changes in this area require a regression test using custom phase and artifact
names. The test must exercise the public production path and fail if a built-in
name or fixed precedence is reintroduced.
