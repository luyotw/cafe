## Checklist

[ ] Read {agent_file} to understand your role and native language
[ ] Read the development guide in {plan_file_path}
[ ] Read the requirements document {spec_file_path}
[ ] Plan implementation steps (planning, not implementation)
[ ] Complete **`## Test List`** in the plan output (`Unit tests (N)` and `Integration tests (M)` with labels mapping to invariants or user journeys; if N or M is 0, explain why)
[ ] Read `src/cafe/data/skills/plan/references/test_invariants_policy.md` when writing Test List items and assertion guidance
[ ] Confirm: Integration test entries describe **user journeys** and **invariant outcomes**, not UI components
[ ] Confirm: Test List items avoid brittle bindings (UI copy, CSS classes, DOM structure, internal state shape) unless the spec explicitly requires them
[ ] Append plan after "## Development Guide" section
[ ] Keep "## Development Guide" section unchanged
[ ] Confirm: Only wrote plans and steps, NO actual code
[ ] Confirm: No code was modified
[ ] Update blackboard and next-step baton to hand off to the next workflow target
{xml_questions_instruction}
