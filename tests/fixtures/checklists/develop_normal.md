## Checklist

[ ] Read src/cafe/data/agents/developer/Nick.md to understand your role and native language
[ ] Carefully read .cafe/issues/test/spec/iteration_001/output.md and .cafe/issues/test/plan/iteration_001/output.md
[ ] Execute development tasks in strict order according to the plan
[ ] Mark each completed task as checked in .cafe/issues/test/plan/iteration_001/output.md (change - [ ] to - [x])
[ ] Follow existing commit message style, commit multiple times if needed
[ ] Do NOT modify commits from other branches
[ ] Confirm: Maximized code reuse by looking for existing patterns and utilities
[ ] Confirm: Commit messages strictly match existing format, language, and structure
[ ] Confirm: All tasks in .cafe/issues/test/plan/iteration_001/output.md are marked [x]
[ ] Read the plan **Test List** (`## Test List` in .cafe/issues/test/plan/iteration_001/output.md); every new or changed test maps to a listed item (update the plan first if scope changed)
[ ] Confirm: New/changed tests assert **invariants** (business rules, journey outcomes)—not UI copy, CSS classes, DOM structure, or internal state shape unless the spec explicitly requires it
[ ] Confirm: Unit tests target extractable pure business logic in shared library modules when applicable; integration tests are named by **user journey** and **invariant outcome**, not by UI component
[ ] Read `src/cafe/data/skills/cafe-plan/references/test_invariants_policy.md` before adding user-visible UI assertions (allowed: a11y roles/labels, test ids, spec-mandated copy)
[ ] Confirm: All tests pass and are not fragile
[ ] Confirm: All commits are made
[ ] Confirm: No pending work remains
[ ] Update blackboard and next-step baton to hand off to the next workflow target



## Agent Guidelines Checklist

[ ] Adhere to the project's coding standards.
[ ] Write comments in the project's customary natural language.
[ ] Follow the project's commit message style.
[ ] Break down tasks using the TDD approach: for each task, write the corresponding test cases before implementation. Ensure all unit tests pass upon the completion of each task.
[ ] Write robust, non-fragile tests: mock only at boundaries (external APIs, I/O), assert on behavior/outcomes not implementation details, avoid exact string matching on error messages.
