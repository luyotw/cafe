## Review Preflight

[ ] Read src/cafe/data/agents/reviewer/Alice.md, every supplied requirement, plan, implementation artifact, workflow or PR feedback, the previous review when applicable, and the correction delta packet; establish the bounded authoritative scope, ensure discovery occurs exactly once by consuming supplied native evidence or otherwise invoking the selected discovery skill, and retain its output only as candidate findings for independent CAFE validation
[ ] Read the requirements specification .cafe/issues/test/spec/iteration_001/output.md
[ ] Read the implementation plan .cafe/issues/test/plan/iteration_001/output.md
[ ] Read PR feedback in (not available) (if exists) to see user feedback and requests
[ ] Record `base SHA`, `Reviewed HEAD`, `Previous Reviewed HEAD`, and the exact correction range in `Review Baseline`; when the previous review is not supplied, boundedly inspect earlier `iteration_*/output.md` siblings below the review directory derived from .cafe/issues/test/review/iteration_001/output.md; if the previous HEAD is missing, is not an ancestor, or has no recognizable evidence, prohibit reuse and review the cumulative `merge-base(develop, HEAD)..HEAD` change
[ ] Inspect `git log develop..HEAD`, the worktree, changed-file hygiene, and commit-message fit once; no new commit or any uncommitted work means development is incomplete, sensitive data or an unwanted committed file is critical, and message-style findings name affected SHAs plus complete non-interactive repair commands

## Triggered Risk Assessment

[ ] Build a cumulative change-and-risk map from the production diff, requirement authority, caller and consumer paths, configuration and defaults, persisted state, identity inputs, mutation targets, locks, snapshots, fixtures, and external effects; instantiate only the fixed obligations whose trigger surface is present
[ ] Add one `Triggered Risk Coverage` row for every applicable obligation, grouping components only when they share one control and listing every member of that equivalence class; record `Risk ID | trigger (path/symbol and reason) | production path | probe (setup -> mutation/action -> expected -> observed) | evidence HEAD | status`, with status limited to `open | closed_fresh | closed_reused | n/a`
[ ] Obtain independent evidence for each triggered obligation through a minimal production-path probe or the strongest bounded alternative; one probe may serve multiple obligations only when it records a distinct assertion and observation for each, and a passing command or raw test name alone is never evidence

| Domain | Trigger surface | Fixed obligations |
| --- | --- | --- |
| **A - Identity / decision binding** | Digest, token, approval, cache key, snapshot identity, or version identity | **A1:** Identity covers every direct or indirect behavior-changing input; changing any such input changes identity or invalidates the decision. **A2:** Check/approval and use/apply consume the same identity; replaced or stale input fails closed. |
| **P - Durable state / lifecycle** | Manifest, journal, checkpoint, migration, resume, retry, or recovery | **P1:** Persisted state is bound to operation, version, source, target, and decision; malformed, stale, replayed, or cross-operation state is rejected before mutation. **P2:** Every durable transition can resume or roll back idempotently after interruption without skipping a decision or acting on the wrong object. |
| **M - Mutation scope / target** | Write, move, delete, install, publish, filesystem mutation, or external-state mutation | **M1:** The target stays within the authorized root or object; ancestor, symlink/alias, and canonicalization behavior cannot retarget it. **M2:** Partial failure affects only the selected scope, preserves unrelated existing data, and leaves recoverable audit state. |
| **R - Resolution / adapters** | Precedence, fallback/default, alias, resolver, or one policy with multiple production consumers | **R1:** Every production consumer uses the same canonical policy and returns the same value or error for the same input; verify with differential parity. **R2:** An invalid highest-authority or configured source fails closed, while missing/default/alias and empty/single/multiple cases never silently change authority. |
| **C - Concurrency / check-use** | Lock, snapshot, transaction publication, or reader/writer overlap | **C1:** Protection spans lookup through content/state consumption so a race cannot replace the checked identity. **C2:** Readers observe a complete before or after state, never mixed or partial state. |
| **X - Executable / capability / resource boundary** | Hook, plugin/skill closure, subprocess, network, untrusted executable input, or long-running work | **X1:** Capability and execution closure are minimal; input cannot introduce undeclared code or data access. **X2:** Time, size, count, depth, output, and cancellation are bounded, and non-success states are explicit without resource amplification. |

Do not expand this table into a new attack taxonomy. At most the twelve fixed obligations above may become rows, and only when their trigger surface is present.

## First-Pass Behavior Review

[ ] Trace each candidate defect to its root cause and inspect changed public callers, supported modes and aliases, empty/single/multiple cardinalities, and applicable lifecycle paths in the same pass; consolidate sibling symptoms into one actionable finding

## Anti-Over-Engineering Review

[ ] Review applicable correctness, error handling, security, performance, persistence, concurrency, fallback, retry/resume, data-loss, and source-of-truth behavior together with code quality and repository fit; require the smallest design that satisfies the approved requirements or recorded planless baseline, apply Dependency ADR vs manifest diff and Dependency hygiene when planned, check new majors released within the last 30 days, reject undeclared dependencies and unnecessary Layering and speculative abstractions, require Explicit cross-component contracts, and catch missing errors, docs, deletions, or committed-file hygiene

## Testing and Invariants Review

[ ] Review targeted tests against acceptance and risk rows plus the supplied Test List or recorded planless baseline: require invariants and user journeys rather than implementation details, applicable pure-logic unit coverage and integration journeys, allowed UI contracts, edge cases, truthful fixtures, and non-fragile assertions; review supplied hook or CI evidence when available, do not require a CAFE verification receipt, and do not run repository-wide validation

## Acceptance Closure

[ ] Compare implementation against .cafe/issues/test/spec/iteration_001/output.md
[ ] Select and record the review baseline: use the approved spec and plan when supplied, but let the latest authoritative user feedback from PR comments or workflow inputs override them where they conflict; otherwise derive a bounded planless baseline from supplied user or issue intent, workflow feedback, code/development summary, commit context, and observable behavior in the change without inventing requirements; request clarification instead of guessing when requirement authority is insufficient
[ ] Build or update one `Acceptance Closure Evidence` row for every acceptance criterion and relevant invariant, recording `ID | claim and source | production path | risk evidence refs | independent evidence | status`; keep requirements claims separate from triggered risk obligations and limit status to `open | closed_fresh | closed_reused | n/a`
[ ] Pass only when every applicable acceptance row is independently evidenced at the recorded review baseline and every referenced risk row satisfies the exit audit; developer assertions, previous `closed` labels, passing command names, and synthetic fixtures or mocks that bypass the reviewed production contract cannot close a row

## Exit Audit

[ ] If the current result is a provisional pass, re-check the cumulative `merge-base(develop, HEAD)..HEAD` change map against every fixed risk trigger and add any missed obligation; if blockers already remain, record why the exit audit was skipped instead of manufacturing pass evidence
[ ] Before routing a pass, freeze the current HEAD and require every triggered obligation to be `closed_fresh` at that HEAD; evidence already produced earlier in this iteration at the same HEAD remains fresh and need not be rerun, but `closed_reused` can never pass the exit audit

## Finalize Review

[ ] Confirm that the reviewer modified no code, then write .cafe/issues/test/review/iteration_001/output.md in this exact order: `## Review Baseline`, `## Todo List`, `## Triggered Risk Coverage`, `## Acceptance Closure Evidence`, `## Outcome`; Todo findings use categorized severity checkboxes with file path and line number, say explicitly when there are no blockers, and identify defects without code solutions, raw unbounded output, or a manufactured verification receipt
[ ] Route missing requirement authority or required user input through the active review step's declared reactive handoff; route implementation, test, evidence, or other blocking findings to `develop`; only a fully closed exit audit proceeds to the next workflow step, then write the next-step baton and keep the response brief

## Basic Principles

[ ] 確認 long-running script 不會造成不可接受的系統負荷或資源放大


## PR Todo List Check
[ ] Read .cafe/issues/test/pr/iteration_001/output.md - this is the todo list from the PR phase
[ ] Check that ALL todo items are marked as completed [x]. If any unchecked items [ ] remain, return needs_changes
