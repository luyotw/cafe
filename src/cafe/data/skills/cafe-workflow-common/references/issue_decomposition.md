# Issue Split Proposals

The active step enables `behavior.allow_issue_decomposition`. Apply these rules
regardless of step name, selected skill, or whether the playbook has a PR step.

- When work contains independently deliverable outcomes, propose separate issues
  with non-overlapping scopes. State the useful outcome retained in the current
  issue and list each proposed issue's title, scope, and dependencies concisely.
- Keep tightly coupled changes together when they form one practical delivery.
  A normal issue needs no decomposition report or justification for staying
  together. Do not add scores, file-count thresholds, mandatory evidence fields,
  exception proofs, extra checkpoints, or validation gates.
- When additional independent work is discovered, finish the current bounded
  change and propose that work separately; do not silently absorb it. Preserve
  the confirmed scope until any required scope decision is resolved through the
  existing handoff route.
- If fixes depend on investigation or measurement, scope the investigation
  first and propose the resulting fixes once they are known.
- Put proposals in the existing step output and surface them in the normal
  handoff. Do not introduce a new artifact, registry, or state machine, and do
  not pause solely because a proposal exists. Use the playbook's existing user
  handoff only when a scope decision or permission is actually needed.
- Phase agents recommend only. They never create issues, update roadmaps, change
  priority or scheduling, or make external project mutations for a split. The
  Driver presents the proposal plainly and uses the existing authorized issue
  creation path. Enabling this behavior grants no external-action authority.

## Examples

Independent outcomes: a request for CSV export and configurable email alerts
can deliver either feature separately. Keep CSV export in the current issue;
propose "Email alerts — configure recipients and delivery rules; dependencies:
none." If a follow-up needs a shared foundation, name that dependency explicitly.

Tightly coupled delivery: an API field, its UI display, and tests for the same
user-visible feature stay together. Multiple files or implementation parts do
not by themselves justify a split; omit the decomposition section entirely.
