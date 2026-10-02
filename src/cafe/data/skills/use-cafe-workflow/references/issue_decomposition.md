# Issue Split Proposals And Project Position

Read this reference when any step proposes an issue split, or when starting or
resuming linked work. Also read `references/strategic_context.md` and
`references/handoffs_and_alignment.md`, relative to the Manager SKILL.md.

## Receive proposals from any step

`behavior.allow_issue_decomposition` enables shared guidance for a step. The
playbook may set a default and individual steps may override it; omitted values
resolve to false. Do not infer this behavior from phase or skill names, and do
not require a spec, plan, or PR step.

Read proposals from the existing step output and normal handoff whenever control
returns, including workflow completion. Present the current issue's retained
scope and a short list of proposed issue titles, scopes, and dependencies. Keep
outcomes independently deliverable and non-overlapping; consult existing open
issues to avoid duplicates. Tightly coupled work stays together without a
report, proof, or extra confirmation. A proposal alone adds no checkpoint or
validation gate.

Additional independent work remains a follow-up while the current bounded
change is completed. If investigation or measurement is needed before fixes
can be defined, scope that investigation first and propose fixes after its
results are known. Do not silently change confirmed product scope.

## Existing authority

Use the existing authorized Manager path for issue creation. Apply existing
scope and external-action authority; ask only when a required decision or
permission is not already authorized. Phase-agent proposals and this behavior
flag grant no authority to create issues or change scope, priority, scheduling,
or external commitments. Record each created issue's scope and dependencies;
no additional mandatory decomposition fields or state are required.

## Reconstructible project position

After preparing, completing, or selecting a linked issue, reconstruct and show
the concise project position from strategic context, confirmed roadmap, issue
state, active workflow records, and existing open issue state. Include:

- project and milestone;
- current issue and current phase;
- completed count and blocked issues;
- next action and required user decision.

Do not create duplicate project state or rely on prior chat memory. A fresh
manager session derives this position again from these durable records.
