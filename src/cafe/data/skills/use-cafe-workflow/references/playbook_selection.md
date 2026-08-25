# Playbook Selection

Read this reference before kickoff when starting new work, resuming work whose
confirmed playbook is missing or stale, or answering why a playbook was chosen.
Playbook selection precedes phase-profile and model selection because the graph
determines which independent responsibilities exist.

## Resolve authoritative selections first

Use the first applicable durable or explicit source:

1. A direct playbook choice from the user in the current thread.
2. On resume, the playbook in the issue's confirmed `issue.yaml` contract.
3. An explicitly authorized issue override in `.cafe/strategic_context.yaml`.
4. The initialized repository's `.cafe/config.yaml`, checked against the
   confirmed repository mandate.
5. The confirmed mandate's repository-wide `playbook_id` when no more specific
   source applies.

A direct change to a persisted choice is allowed, but it invalidates the old
kickoff contract and requires full reconfirmation. Conflicting durable sources
are not a reason to guess; show the conflict and ask one focused question.

## Select when no authoritative choice exists

First assess the issue nature, scale, risk, acceptance surface, and repository
instructions. Run `cafe playbook list`, then inspect only plausible candidates
with `cafe playbook show <id>`. Compare the actual step graphs and phase skills;
do not infer behavior from a playbook name or copy a playbook used by another
issue.

Choose the narrowest graph that still assigns every required responsibility to
an appropriate independent phase. Evaluate:

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
repository evidence.

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
owner. Record that justification rather than silently omitting QA.

When both a base and QA variant are plausible, compare their graphs directly.
Prefer the QA variant when the evidence above applies; otherwise prefer the base
variant and explain why its verification and review phases are sufficient.

## Record and reconfirm

The kickoff contract must include `playbook_selection_rationale` containing:

- the authoritative source or the repository and issue evidence used;
- the required phase responsibilities, including the QA decision;
- the closest plausible alternative and why it was rejected.

If evidence cannot safely distinguish the candidates and the difference affects
scope, cost, confirmation stops, external effects, or acceptance confidence, ask
one focused question. Otherwise recommend one graph in the kickoff and let the
complete kickoff confirmation approve it.

Reassess before the first execution if issue facts or repository instructions
change. Any playbook change requires a freshly rendered and confirmed contract.
