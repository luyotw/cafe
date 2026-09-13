# Engineering Guidelines

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
