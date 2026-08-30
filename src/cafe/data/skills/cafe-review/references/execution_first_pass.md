## First-Pass Behavior Review

[ ] Trace each candidate defect to its root cause and inspect changed public callers, supported modes and aliases, empty/single/multiple cardinalities, and applicable lifecycle paths in the same pass; consolidate sibling symptoms into one actionable finding

## Anti-Over-Engineering Review

[ ] Review applicable correctness, error handling, security, performance, persistence, concurrency, fallback, retry/resume, data-loss, and source-of-truth behavior together with code quality and repository fit; require the smallest design that satisfies the approved requirements or recorded planless baseline, apply Dependency ADR vs manifest diff and Dependency hygiene when planned, check new majors released within the last 30 days, reject undeclared dependencies and unnecessary Layering and speculative abstractions, require Explicit cross-component contracts, and catch missing errors, docs, deletions, or committed-file hygiene

## Testing and Invariants Review

[ ] Review targeted tests against acceptance and risk rows plus the supplied Test List or recorded planless baseline: require invariants and user journeys rather than implementation details, applicable pure-logic unit coverage and integration journeys, allowed UI contracts, edge cases, truthful fixtures, and non-fragile assertions; review supplied hook or CI evidence when available, do not require a CAFE verification receipt, and do not run repository-wide validation
