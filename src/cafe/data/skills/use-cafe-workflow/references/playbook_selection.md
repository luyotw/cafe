# Playbook Selection

Read this reference before kickoff when starting new work, resuming work whose
confirmed playbook is missing or stale, or answering why a playbook was chosen.
Playbook selection precedes phase-profile and model selection because the graph
determines which independent responsibilities exist.

## Resolve authoritative selections first

Use the first applicable durable or explicit source:

1. A direct playbook choice from the user in the current thread.
2. On resume, the playbook in the issue's confirmed `issue.yaml` contract.

A Manager proposal or an unconfirmed kickoff table is not a direct user choice.
Do not promote a previous recommendation into selection authority merely
because it appears in a draft contract.

A direct change to a persisted choice is allowed, but it invalidates the old
kickoff contract and requires full reconfirmation. Conflicting durable sources
are not a reason to guess; show the conflict and ask one focused question.

`.cafe/config.yaml` and `.cafe/strategic_context.yaml` are not playbook-selection
sources. Treat any legacy `settings.playbook`, top-level `playbook`, or
`playbook_id` stored in those repository-level files as non-authoritative; do
not copy, refresh, or use it as the current issue's selection. Repository
instructions and strategic documents may constrain the assessment below, but a
repository-wide playbook ID must not replace that assessment.

## Select when no authoritative choice exists

First assess the issue nature, scale, risk, acceptance surface, and repository
instructions from confirmed current scope. Unconfirmed speculative future work must not add
responsibilities or phases to the current recommendation; handle it through the
existing clarification or permission boundary only if it becomes current.

Explicit current scope and exclusions supersede older issue descriptions and
earlier Manager proposals. When scope narrows, reassess the responsibilities
before proposing the graph again; excluded work must not justify extra phases.
Keep applicable repository requirements unless the user explicitly changes them.

Run `prepare_kickoff.py discover` using one request file and use its compact
index of every valid effective playbook across the project, Global, and builtin
catalogs. Catalog precedence makes a same-id project override the one effective
candidate; never evaluate its shadowed definitions separately. Inspect a
specific candidate with `cafe playbook show <id>` only when its indexed facts or
diagnostics leave a material question unresolved; do not repeat list/show/read
cycles for every candidate.
A candidate with missing applicability is ineligible for automatic
recommendation: report the exclusion and tell its author to add the complete
contract and run `cafe playbook validate <id> --strict`. Do not infer missing
conditions from the candidate's id, name, source, graph, or phase skills. More
generally, do not infer behavior from a playbook name or copy a playbook used by
another issue.

Derive the required responsibilities and boundaries from the confirmed scope.
Reject candidates whose resolved graph or phase skills are insufficient before
comparing applicability. Applicability cannot compensate for a missing
responsibility. For the remaining candidates, compare the graph and declared
selection intent, apply the native-subagent preference below, then choose the
smallest sufficient graph among equally suitable candidates. Candidate names
and catalog sources are not ranking signals. Evaluate:

- workflow domain and outcome, such as product development, hotfix, incident,
  research, or editorial work;
- unconfirmed requirements or architecture that require spec or plan ownership;
- repository-mandated development methods, including test-first or TDD rules;
- acceptance checks that must be independent from implementation and code review;
- urgency, rollback, external side effects, and how difficult a regression is to
  observe or reverse;
- scheduled confirmation gates and the additional execution cost of the graph.

Do not select a simpler graph merely because the code change is small when a
repository rule or acceptance boundary requires an omitted phase. Do not select
a larger graph merely because it exists; every added phase needs issue or
repository evidence. Compare the closest alternatives internally; explain a
material tradeoff when the user needs to choose. If no eligible candidate is sufficient, state the
uncovered requirements and ask the user for an explicit decision instead of
choosing a familiar or larger playbook.

Separate spec ownership needs evidence of an independent owner or artifact
boundary. Unresolved requirements or architecture alone do not require separate
spec and plan phases when a joint phase with a planning partner covers them.

## Independent QA decision

Select a QA-capable candidate when any of these apply:

- the user or repository instructions require an independent QA, acceptance, or
  test-runner agent;
- acceptance is black-box or environment-dependent across hosts, deployments,
  browsers, devices, permissions, or other runtime variants that implementation
  and code review do not independently own;
- a false pass can create an externally consequential production regression that
  is difficult to detect from unit tests or diff review;
- external-side-effect acceptance needs independent evidence before publication.

Ordinary automated tests do not by themselves require a QA phase. A non-QA
candidate is acceptable only when develop verification plus independent review
fully covers the acceptance boundary and no repository policy requires another
owner. Make that judgment before proposing the graph; no rationale field is required.

Cross-module changes and platform-specific conditions alone do not require QA.
When recommending QA, identify the concrete acceptance check that needs another
owner or the explicit user or repository requirement; do not infer that need
from the number of layers touched.

When both a base and QA variant are plausible, compare their graphs directly.
Prefer the QA variant when the evidence above applies; otherwise prefer the base
variant when its verification and review phases are sufficient.

## Prefer verified native subagents

When candidates cover the same required responsibilities and QA boundaries,
prefer a graph that delegates planning or review to native subagents if the
intended execution chains support those delegations. Use the index's
`native_subagent_steps` and each profile's `required_tools`, resolved across all
iteration variants and shared, role, and step workflow skills. Never detect this
behavior from a playbook name or from an allowed tool alone.

Assess capability provisionally before recommending the graph, then verify it
for every primary and configured fallback of the affected steps during model
preflight. Accept the user's explicit capability confirmation for the current
setup or successful native execution evidence for the installed CLI, selected
model, and effective settings. Check these accepted sources before declaring
capability unknown. Missing a fresh native probe is not missing capability
evidence when the user's confirmation covers the intended setup and delegation.
Do not demand a redundant probe solely because the evidence is user-confirmed.
Documentation, a version number, model login,
or an ordinary model probe alone does not prove usable delegation.

If capability is unknown, inspect the affected phase's resolved skills and use
a bounded disposable probe matching its actual delegation requirements.
`Agent` alone requires native delegation; it does not imply parallel reviewers.
For a phase needing one read-only planning partner, verify one native launch,
a successful result, and parent collection. For a phase requiring two parallel
read-only reviewers, verify both launches before either completes, both
successful results, and parent collection of both. A single-partner phase must
not be rejected solely because two concurrent reviewers are unavailable.
Honor native workspace trust and custom-agent acknowledgment. An unregistered
probe definition is a setup failure, not proof that the provider lacks subagents.
Require native tool events rather than a parent claim of delegation. A missing
result or permission denial is a failure; serial execution fails a phase that
requires parallelism. Keep evidence local; recheck when a changed CLI,
model, or setting falls outside the accepted evidence, or a live failure
contradicts it. Confirmation for one setup does not establish capability for
uncovered fallback entries or different delegation requirements. Do not hardcode a provider
ranking or maintain a second runtime capability registry here.

The preference cannot override an explicit or persisted choice, repository
methods, independent QA, or mandatory confirmation gates. If preflight cannot
verify a required delegation, reconsider the unconfirmed chain or recommend an
otherwise sufficient graph through the existing kickoff decision; do not
silently replace native subagents with shell-launched agents or omit review.

## Record and reconfirm

The kickoff confirms the selected playbook, not a selection report. Keep the
repository assessment and rejected alternatives out of the contract. Explain
them on request or when a material tradeoff requires a user decision.

If evidence cannot safely distinguish the candidates and the difference affects
scope, cost, confirmation stops, external effects, or acceptance confidence, ask
one focused question. Otherwise recommend one graph in the kickoff and let the
complete kickoff confirmation approve it.

Reassess before the first execution if issue facts or repository instructions
change. Any playbook change requires a freshly rendered and confirmed contract.

After the complete kickoff is confirmed, persist the effective playbook in
`.cafe/issues/<issue-name>/issue.yaml` under its generic lifecycle, while the
separate Manager-owned subset is persisted in `manager/contract.json`. Neither
authority duplicates the other. Never persist the effective issue contract in `.cafe/config.yaml` or
`.cafe/strategic_context.yaml`, even when the same playbook has been selected
for several issues.
