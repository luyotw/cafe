## First-Pass Behavior Review

[ ] Trace each candidate defect to its root cause and inspect changed public callers, supported modes and aliases, empty/single/multiple cardinalities, and applicable lifecycle paths in the same pass; consolidate sibling symptoms into one actionable finding
[ ] Review all risks applicable to the change in one pass: correctness, error handling, security, performance, persistence, concurrency, fallback, retry/resume, data-loss, and source-of-truth behavior
[ ] Review code quality and repository fit in one pass: existing style and utilities, reuse, duplication, readability, missing errors/prompts/docs/examples, comment claims, deletions, unused code, and committed-file hygiene

## Anti-Over-Engineering Review

[ ] Confirm the implemented design is the smallest design that satisfies the approved requirements or recorded planless baseline; apply Dependency ADR vs manifest diff and Dependency hygiene when a plan is supplied, treating a package not declared there as undeclared, and without a plan require concrete necessity and no simpler existing alternative; check new majors released within the last 30 days, Layering and speculative abstractions, and Explicit cross-component contracts

## Testing and Invariants Review

[ ] Review targeted tests against closure rows and the supplied Test List or recorded planless baseline: require invariants and user journeys rather than implementation details, applicable pure-logic unit coverage and integration journeys, allowed UI contracts, edge cases, truthful fixtures, and non-fragile/non-flaky assertions; review supplied Git-hook or CI evidence when available, do not require a CAFE verification receipt, and do not run repository-wide validation
