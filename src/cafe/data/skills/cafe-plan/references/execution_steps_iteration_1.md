## Checklist

[ ] Read {agent_file} to understand your role and native language
[ ] Read the development guide in {output_file}
[ ] Read the requirements document {spec_file}
[ ] Before choosing an unset runtime/deployment architecture, treat the user as non-technical by default: check existing repo/spec/conversation answers, then ask only the missing plain-language usage questions and recommend one suitable default before technical details
[ ] Confirm the plan does not assume a fixed IP, an always-on personal computer/NAS, self-managed server expertise, or authorization to adopt/pay for/deploy an external service
[ ] Plan implementation steps (planning, not implementation)
[ ] Fill required sections **Negative space**, **Layering map**, and **Dependency ADR** (explicit "none" / "no new dependencies" if applicable — empty placeholders are incomplete)
[ ] If `.cafe/strategic_context.yaml` has `documents.principles.path` with `status: exists`, read that file and ground Negative space and Dependency ADR; otherwise leave principles cross-refs blank
[ ] For any new major in Dependency ADR, note if released within the last 30 days and justify or pick a stable alternative
[ ] Complete **`## Test List`** in the plan output (`Unit tests (N)` and `Integration tests (M)` with labels mapping to invariants or user journeys; if N or M is 0, explain why)
[ ] Read `src/cafe/data/skills/cafe-plan/references/test_invariants_policy.md` when writing Test List items and assertion guidance
[ ] Confirm: Integration test entries describe **user journeys** and **invariant outcomes**, not UI components
[ ] Confirm: Test List items avoid brittle bindings (UI copy, CSS classes, DOM structure, internal state shape) unless the spec explicitly requires them
[ ] Preserve source requirement wording in ordinary Markdown; do not add packet-specific IDs or duplicate semantic contracts
[ ] Append plan after "## Development Guide" section
[ ] Keep "## Development Guide" section unchanged
[ ] Confirm: Only wrote plans and steps, NO actual code
[ ] Confirm: No code was modified
[ ] Write the next-step baton to hand off to the next workflow target; the runtime updates blackboard
{xml_questions_instruction}
