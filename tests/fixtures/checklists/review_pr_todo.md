## Review Preflight

[ ] Read src/cafe/data/agents/reviewer/Alice.md and every supplied requirement, plan, implementation artifact, and feedback item; establish the bounded scope for this review iteration
[ ] Read the requirements specification .cafe/issues/test/spec/iteration_001/output.md
[ ] Read the implementation plan .cafe/issues/test/plan/iteration_001/output.md
[ ] Read PR feedback in (not available) (if exists) to see user feedback and requests
[ ] Inspect `git log develop..HEAD` and the worktree once: no new commit or any uncommitted work means development is incomplete; sensitive data or an unwanted committed file is a critical finding
[ ] Compare branch commit messages with recent `develop` history in one pass; when style differs, report the affected SHAs, expected language/body style, and complete non-interactive repair commands

## Acceptance Closure

[ ] Compare implementation against .cafe/issues/test/spec/iteration_001/output.md
[ ] Select and record the review baseline: use the approved spec and plan when supplied, but let the latest authoritative user feedback from PR comments or workflow inputs override them where they conflict; otherwise derive a bounded planless baseline from supplied user or issue intent, workflow feedback, code/development summary, commit context, and observable behavior in the change without inventing requirements; request clarification instead of guessing when requirement authority is insufficient
[ ] Build or update one closure row for every acceptance criterion and relevant invariant in that baseline; each row records its source, applicable production entry point, consumer, or artifact, independent evidence, and open/closed status
[ ] Trace each runtime-behavior row through the real production caller path, including configuration/default resolution and applicable persistence, concurrency, fallback, retry, resume, or takeover behavior; for trust-sensitive, compatibility, source-precedence, data-loss, or external-state claims, run one original bounded probe through the production path, or explain why it is infeasible and use the strongest available alternative evidence
[ ] Pass only when every closure row is independently evidenced; developer assertions and test names are not proof, and synthetic fixtures or mocks that bypass or omit the reviewed contract cannot close a row

## First-Pass Behavior Review

[ ] Trace each candidate defect to its root cause and inspect changed public callers, supported modes and aliases, empty/single/multiple cardinalities, and applicable lifecycle paths in the same pass; consolidate sibling symptoms into one actionable finding
[ ] Review all risks applicable to the change in one pass: correctness, error handling, security, performance, persistence, concurrency, fallback, retry/resume, data-loss, and source-of-truth behavior
[ ] Review code quality and repository fit in one pass: existing style and utilities, reuse, duplication, readability, missing errors/prompts/docs/examples, comment claims, deletions, unused code, and committed-file hygiene

## Anti-Over-Engineering Review

[ ] Confirm the implemented design is the smallest design that satisfies the approved requirements or recorded planless baseline; apply Dependency ADR vs manifest diff and Dependency hygiene when a plan is supplied, treating a package not declared there as undeclared, and without a plan require concrete necessity and no simpler existing alternative; check new majors released within the last 30 days, Layering and speculative abstractions, and Explicit cross-component contracts

## Testing and Invariants Review

[ ] Review targeted tests against closure rows and the supplied Test List or recorded planless baseline: require invariants and user journeys rather than implementation details, applicable pure-logic unit coverage and integration journeys, allowed UI contracts, edge cases, truthful fixtures, and non-fragile/non-flaky assertions; review supplied Git-hook or CI evidence when available, do not require a CAFE verification receipt, and do not run repository-wide validation

## Finalize Review

[ ] Confirm that the reviewer modified no code
[ ] Write a brief `## Todo List` to .cafe/issues/test/review/iteration_001/output.md; findings use categorized checkbox items with file path and line number, and `Acceptance Closure Evidence` has one concise row per criterion or invariant naming its source, applicable production entry point, consumer, or artifact, evidence, and status; identify defects without providing code solutions or manufacturing a verification receipt
[ ] Route missing requirement authority or required user input/authorization through a reactive user handoff declared by the active review step (builtin default: `need_clarification`), never an undeclared intent; route implementation, test, developer-suppliable evidence gaps, or other blocking findings to `develop`; only a fully closed review proceeds to the next workflow step
[ ] Write the next-step baton for that result; keep the response brief because workflow transitions are controlled by the baton

## Basic Principles

[ ] 確認 long-running script 不會造成不可接受的系統負荷或資源放大


## PR Todo List Check
[ ] Read .cafe/issues/test/pr/iteration_001/output.md - this is the todo list from the PR phase
[ ] Check that ALL todo items are marked as completed [x]. If any unchecked items [ ] remain, return needs_changes
