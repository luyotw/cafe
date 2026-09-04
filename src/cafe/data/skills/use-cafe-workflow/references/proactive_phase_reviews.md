# Proactive Phase Reviews

Read this reference before assessing, confirming, preparing, reconciling, or
accepting a phase selected for proactive review. The driver owns the policy and
uses the skill-local `scripts/proactive_review.py` helper only for structural
validation, current live-contract checks, and bounded issue-local evidence.

## Assess and confirm the smallest sufficient useful set

For every resolved `agent` or `hybrid` phase, record one selected or excluded
decision in playbook order. Evaluate ambiguity, novelty, blast radius,
architecture/security/permission/persistence/concurrency/migration risk,
durable or public contracts, timely equivalent downstream independent review,
late correction/discarded work, and initial plus reasonably foreseeable
re-review token/latency cost. Explain each phase-specific decision. Neither
all phases nor no phases is a default; equivalent downstream coverage justifies
exclusion only when it has comparable independence and evidence soon enough.

Every selected row has exactly one confirmed CLI/model reviewer, truthful
`before_next_phase` or `non_gating` ordering, an estimate or bounded band for
initial tokens and latency, material assumptions, and delay impact. A selected
row cannot omit correction/re-review disclosure: either provide the same cost
shape when reasonably foreseeable or explain why it cannot be estimated. An
empty set is valid only when all exclusions are explicit and explained.

Render the whole proposal through the kickoff formatter before any issue state
exists. The digest covers canonical rendered policy only. After explicit user
confirmation, prepare the issue and atomically activate the exact policy with
the user identity/time metadata. Do not persist a candidate, transcript,
catalog snapshot, report ledger, or reviewer default. Selection, reviewer,
ordering, or material-cost changes require a complete replacement proposal and
full reconfirmation; failed or stale replacement leaves the old contract active.

## Prepare a complete independent review

After a selected phase has durable output, load the active contract through the
shared helper before preparing the review. A missing, copied, malformed, or
stale issue/playbook/inventory/boundary contract is reconfirmation-required,
never an empty plan or clean result. Supply the confirmed policy and exact
reviewer, output identity and complete output, confirmed requirements, accepted
upstream artifacts, bounded relevant repository evidence, and the current
correction history. Bind the current repository HEAD and changed-state identity
to those inputs so later repository drift makes the obligation pending.
Missing or stale inputs make the obligation pending.

The confirmed reviewer performs a fresh whole-artifact pass. It assesses:

- missing required work, decisions, constraints, or evidence;
- excess work, abstractions, dependencies, complexity, or follow-on scope; and
- whether the output is proportionate to the confirmed objective.

Each blocker must be grounded in observable artifact evidence plus a confirmed
requirement, accepted upstream artifact, or established contract. Return all
observable blockers together, identifying the violated constraint, expected
outcome, and focused verification. Preference-only observations are not
blockers. Never review the proactive-review report itself.

## Correct, re-review, and retain bounded evidence

Route one consolidated blocker set only through a correction target already
authorized by the current playbook and handoff contract. The driver and
reviewer never edit generated phase artifacts. If no route exists, stop for the
user; do not invent a phase, target, intent, or authority. Mark downstream work
that relied on blocked output for its existing owner to revalidate, correct, or
discard.

Reviewer failure, unavailability, partial output, a reviewer mismatch, missing
inputs, or output identity drift remains pending. Retry only the confirmed
reviewer or use the existing reconfirmation path; never substitute one. A
corrected output starts a new current episode and receives another complete
review. Keep one current episode per selected phase with unresolved blockers,
the immediately relevant resolution status, correction delta, changed
assumptions, evidence to recheck, and affected downstream work. Compact clean
episodes to the current output, reviewer, scope assessment, and a resolved
summary digest. Serialize each shared state read-modify-write transition so
concurrent selected phases cannot discard one another's current evidence. Do
not create per-output reports or append-only history.

`before_next_phase` may delay only at an existing graph boundary. `non_gating`
never controls a continuous worker or callback, but either ordering requires
current clean evidence before driver acceptance or confirmation presentation.
A clean review does not satisfy or waive user-required/driver-confirmable
confirmation, a mandatory HumanTask, permission, capability, scope, or other
authority decision. It does not replace the workflow review phase or final
convergent PR review.
