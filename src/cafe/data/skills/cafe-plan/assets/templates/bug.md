# Bug Fix Implementation Plan

## Bug Description

**What's wrong:**
[Describe the bug behavior]

**Expected behavior:**
[What should happen instead]

**Affected areas:**
- [File/component 1]
- [File/component 2]

## Issue Decomposition Assessment

- Decision: `keep` or `split`
- Rationale: [Repository evidence for keeping or splitting delivery]
- Current issue scope: [Independently acceptable outcome to deliver now]
- Trigger: [none, product scope, or implementation scope]

### Proposed follow-up issues

| Title | Goal | Depends on | Scope boundary | Non-goals | Definition of Done |
| --- | --- | --- | --- | --- | --- |

## Negative space

> Fill after investigation. List obvious-but-declined additions (extra deps, refactors, new abstractions) with one-line reasons. If N/A: "No scope expansion beyond the bug fix."

## Layering map

> Fill after root-cause analysis. Map business logic, persistence, and UI to concrete paths.

## Dependency ADR

> Fill after investigation. List any new runtime/dev deps with why, alternatives, and requirement served — or **"No new dependencies expected."** Flag new majors released within **30 days** unless justified.

---

## Test List

### Unit tests (N)
1. **Label** — Invariant: … — Scope: pure business logic / shared library module

### Integration tests (M)
1. **Label** — Journey: … — Invariant outcome: … — Boundary: system-level behavior (not per-component)

_(If N or M is 0, one sentence explains why.)_

## Task Breakdown

> **Instructions for Developer:** Update this checklist as you progress. Replace placeholders with actual implementation steps once you identify the root cause.

### Phase 1: Investigation
- [ ] Reproduce the bug locally
- [ ] Add logging/debugging to narrow down the issue
- [ ] Identify the root cause
- [ ] Update the "Root Cause" section above

### Phase 2: Test Preparation
- [ ] Write a test case that reproduces the bug
- [ ] Verify the test fails before the fix (confirms bug exists)
- [ ] Document expected test behavior

### Phase 3: Implementation
- [ ] **[UPDATE THIS]** - Replace with actual fix steps after investigation
- [ ] **[UPDATE THIS]** - Add specific code changes needed
- [ ] **[UPDATE THIS]** - Add more steps as needed
- [ ] Add error handling if needed

### Phase 4: Verification
- [ ] Verify the bug-reproducing test now passes
- [ ] Run all existing tests to check for regressions
- [ ] Test edge cases manually
- [ ] Add additional test cases if needed

### Phase 5: Cleanup
- [ ] Update code comments
- [ ] Update documentation if necessary
- [ ] Remove any debug code
- [ ] Commit changes

### Phase 6: Definition of Done (DoD)
Copy the DoD items from the spec's Acceptance Criteria section (lines marked with `✅ **DoD:**`) and verify each one:
- [ ] The reported bug no longer reproduces
- [ ] No regressions in existing functionality

## Authoritative Delivery IDs

Keep these IDs on the matching architecture, test, ADR, and top-level task as
the plan evolves; the downstream contract must contain the same IDs and task state.

- **ARCH-001** — [Architecture boundary]
- **INV-001** — [Stable invariant]
- **UT-001** — [Planned unit test]
- **ADR-001** — [Dependency decision]
- [ ] **TASK-001** — [Top-level executable task]

## Downstream Contract

- Contract-Version: `1`
- Artifact-Kind: `plan`

### Architecture Boundaries
| ID | Location | Responsibility |
| --- | --- | --- |
| ARCH-001 | [Path] | [Boundary] |
### Invariants
| ID | Statement |
| --- | --- |
| INV-001 | [Stable invariant] |
### Test List
| ID | Type | Covers |
| --- | --- | --- |
| UT-001 | unit | INV-001 |
### Dependency ADR References
| ID | Decision | Requirement / invariant |
| --- | --- | --- |
| ADR-001 | [Decision] | INV-001 |
### Task Status
| ID | Status | Summary | Depends On |
| --- | --- | --- | --- |
| TASK-001 | pending | [Top-level task] | — |
