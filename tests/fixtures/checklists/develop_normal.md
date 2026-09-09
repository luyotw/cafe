## Checklist

[ ] Read src/cafe/data/agents/developer/Nick.md to understand your role and native language
[ ] Carefully read .cafe/issues/test/spec/iteration_001/output.md and .cafe/issues/test/plan/iteration_001/output.md
[ ] Execute development tasks in strict order according to the plan
[ ] Mark each completed task as checked in .cafe/issues/test/plan/iteration_001/output.md (change - [ ] to - [x])
[ ] Keep the plan contract valid when marking completion: set matching `Task Status` rows to `completed` (never `done`)
[ ] Follow existing commit message style, commit multiple times if needed
[ ] Do NOT modify commits from other branches
[ ] Confirm: Maximized code reuse by looking for existing patterns and utilities
[ ] Confirm: Commit messages strictly match existing format, language, and structure
[ ] Confirm: All tasks in .cafe/issues/test/plan/iteration_001/output.md are marked [x]
[ ] Read the plan **Test List** (`## Test List` in .cafe/issues/test/plan/iteration_001/output.md); every new or changed test maps to a listed item (update the plan first if scope changed)
[ ] Confirm: New/changed tests assert **invariants** (business rules, journey outcomes)—not UI copy, CSS classes, DOM structure, or internal state shape unless the spec explicitly requires it
[ ] Confirm: Unit tests target extractable pure business logic in shared library modules when applicable; integration tests are named by **user journey** and **invariant outcome**, not by UI component
[ ] Read `src/cafe/data/skills/cafe-plan/references/test_invariants_policy.md` before adding user-visible UI assertions (allowed: a11y roles/labels, test ids, spec-mandated copy)
[ ] Run targeted tests for new or changed behavior with bounded output; when a plan is supplied, map them to its Test List; do not rerun repository full-suite, coverage, release, or pre-push gates from this phase
[ ] Confirm: All commits are made
[ ] Confirm: Repository pre-commit hooks ran for normal commits when configured; any user-authorized bypass is recorded in the development summary
[ ] Confirm: The worktree is clean
[ ] Confirm: Targeted checks pass and tests are not fragile
[ ] Confirm: No pending work remains
[ ] Write a non-empty development summary to .cafe/issues/test/develop/iteration_001/output.md
[ ] Write the next-step baton to hand off to the next workflow target; the runtime updates blackboard


## Basic Principles

[ ] 新增或修改 declaration、設定欄位或共用 runtime 參數時，追蹤 `schema/validation → effective resolver/defaults → production callers` 的完整接線；明確檢查適用的 primary、backup、retry 與 resume 路徑，不適用者需留下理由
[ ] 為上述接線新增至少一個經過 public caller path 的 regression test，且移除任一必要 forwarding 時該測試必須失敗；只直接測 helper 或手動傳值不足以證明 production 接線
[ ] 確認 long-running script 不會造成不可接受的系統負荷或資源放大


## Agent Guidelines Checklist

[ ] Adhere to the project's coding standards.
[ ] Write comments in the project's customary natural language.
[ ] Follow the project's commit message style.
[ ] Break down tasks using the TDD approach: for each task, write the corresponding test cases before implementation. Ensure all unit tests pass upon the completion of each task.
[ ] Write robust, non-fragile tests: mock only at boundaries (external APIs, I/O), assert on behavior/outcomes not implementation details, avoid exact string matching on error messages.
