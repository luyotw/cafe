## Checklist

[ ] Read {agent_file} to understand your role and native language
{normal_plan_context}[ ] Follow existing commit message style, commit multiple times if needed
[ ] Do NOT modify commits from other branches
[ ] Confirm: Maximized code reuse by looking for existing patterns and utilities
[ ] Confirm: Commit messages strictly match existing format, language, and structure
{normal_plan_verification}[ ] Confirm: New/changed tests assert **invariants** (business rules, journey outcomes)—not UI copy, CSS classes, DOM structure, or internal state shape unless the spec explicitly requires it
[ ] Confirm: Unit tests target extractable pure business logic in shared library modules when applicable; integration tests are named by **user journey** and **invariant outcome**, not by UI component
[ ] Read `src/cafe/data/skills/cafe-plan/references/test_invariants_policy.md` before adding user-visible UI assertions (allowed: a11y roles/labels, test ids, spec-mandated copy)
[ ] Confirm: All tests pass and are not fragile
[ ] Confirm: All commits are made
[ ] Confirm: No pending work remains
[ ] Update blackboard and next-step baton to hand off to the next workflow target
{xml_questions_instruction}
