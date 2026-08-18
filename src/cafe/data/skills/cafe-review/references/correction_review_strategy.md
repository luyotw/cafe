## Correction Review Strategy

[ ] Read the previous review output and the correction delta before broader inspection; re-verify every prior finding item by item
[ ] For each corrected root cause, inspect the directly related equivalence classes in one pass: supported modes and aliases, empty/single/multiple cardinalities, entry points, and persistence/resume/takeover paths when applicable
[ ] Consolidate remaining violations of one invariant into one finding with all affected boundaries listed; do not drip-feed sibling cases across later iterations
[ ] After closing prior findings, review the correction diff and its direct callers, consumers, tests, and contracts for regressions; do not restart an unrelated repository-wide audit
[ ] Add a finding outside the correction surface only when it is a critical correctness, security, data-loss, or source-of-truth risk, and explain why the correction exposed it
[ ] Before passing, perform one bounded closure sweep across the acceptance criteria and invariants touched by the correction so an obvious sibling boundary is not deferred to another iteration
