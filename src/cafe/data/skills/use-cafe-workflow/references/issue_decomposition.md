# Issue Decomposition And Project Position

Read this reference when starting or resuming linked work, or before the driver
confirms a spec or plan containing an issue-decomposition assessment. Also read
`strategic_context.md` and `handoffs_and_alignment.md`.

## Assessments before confirmation

The requirements and planning stages use this stable assessment structure:

- Decision: `keep` or `split`
- Rationale
- Current issue scope
- Trigger

| Title | Goal | Depends on | Scope boundary | Definition of Done |
| --- | --- | --- | --- | --- |

Before confirming, compare the newest assessment with the confirmed
requirement, relevant strategic documents, repository evidence, and existing
open issues. For `keep`, continue the existing confirmation flow without an
extra decomposition prompt.

For `split`, reject proposals that are vague, overlapping, or unsupported.
Require a useful, independently acceptable outcome for the current issue and
non-overlapping follow-up outcomes before external coordination. A planning
assessment may refine delivery order but must not silently change confirmed
product scope.

## Authority and delivery gate

Resolve the mandate and required authority first. Ask the user only if the
proposal changes an escalated decision, including product scope, priority,
cost, or external commitments. Only then coordinate follow-up issue creation or
updates, roadmap updates, and dependency order through existing trusted
mechanisms.

When `split` leaves the current issue too broad, the current issue is narrowed
to an independently acceptable, deliverable, and reviewable outcome. It must
not enter develop until that is true. Do not treat an agent-authored proposal as
authority for an external mutation.

## Reconstructible project position

After preparing, completing, or selecting a linked issue, reconstruct and show
the concise project position from strategic context, confirmed roadmap, issue
state, active workflow records, and existing open issue state. Include:

- project and milestone;
- current issue and current phase;
- completed count and blocked issues;
- next action and required user decision.

Do not create duplicate project state or rely on prior chat memory. A fresh
driver session derives this position again from these durable records.
