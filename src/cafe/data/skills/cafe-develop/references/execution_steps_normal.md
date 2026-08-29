## Checklist

[ ] Read {agent_file} to understand your role and native language
{normal_plan_context}[ ] Follow existing commit message style, commit multiple times if needed
[ ] Do NOT modify commits from other branches
[ ] Confirm: Maximized code reuse by looking for existing patterns and utilities
[ ] Confirm: Commit messages strictly match existing format, language, and structure
{normal_plan_verification}[ ] Confirm: New/changed tests assert **invariants** (business rules, journey outcomes)—not UI copy, CSS classes, DOM structure, or internal state shape unless the spec explicitly requires it
[ ] Confirm: Unit tests target extractable pure business logic in shared library modules when applicable; integration tests are named by **user journey** and **invariant outcome**, not by UI component
[ ] Read `src/cafe/data/skills/cafe-plan/references/test_invariants_policy.md` before adding user-visible UI assertions (allowed: a11y roles/labels, test ids, spec-mandated copy)
[ ] Run targeted tests for new or changed behavior with bounded output; when a plan is supplied, map them to its Test List; do not rerun repository full-suite, coverage, release, or pre-push gates from this phase
[ ] Confirm: All commits are made
[ ] Confirm: Repository pre-commit hooks ran for normal commits when configured; any user-authorized bypass is recorded in the development summary
[ ] Confirm: The worktree is clean
[ ] Confirm: Targeted checks pass and tests are not fragile
[ ] Confirm: No pending work remains
[ ] Write a non-empty development summary to {output_file}
[ ] Write the next-step baton to hand off to the next workflow target; the runtime updates blackboard
{xml_questions_instruction}
