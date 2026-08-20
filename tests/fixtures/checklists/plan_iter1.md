## Checklist

[ ] Read src/cafe/data/agents/developer/Nick.md to understand your role and native language
[ ] Read the development guide in .cafe/issues/test/plan/iteration_001/output.md
[ ] Read the requirements document .cafe/issues/test/spec/iteration_001/output.md
[ ] Before choosing an unset runtime/deployment architecture, treat the user as non-technical by default: check existing repo/spec/conversation answers, then ask only the missing plain-language usage questions and recommend one suitable default before technical details
[ ] Confirm the plan does not assume a fixed IP, an always-on personal computer/NAS, self-managed server expertise, or authorization to adopt/pay for/deploy an external service
[ ] Plan implementation steps (planning, not implementation)
[ ] Confirm the proposed design is the smallest design that satisfies the requirements, with no speculative scope or abstractions
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

## Interactive Q&A Questions

[ ] If user clarification is needed: write questions to .cafe/issues/test/plan/iteration_001/questions.xml in the following XML format and hand off to `user` with need_clarification:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<questions>
  <question id="1">
    <title>Your question text here?</title>
    <options>
      <option>Suggested answer 1</option>
      <option>Suggested answer 2</option>
      <option>Suggested answer 3</option>
    </options>
  </question>
  <question id="2">
    <title>Another question?</title>
    <options>
      <option>Option A</option>
      <option>Option B</option>
    </options>
  </question>
</questions>
```

Rules:
- Write all questions and options in your native language (not English unless that is your native language)
- Root element must be `<questions>`
- Each question must have a unique `id` attribute, a `<title>`, and `<options>` with at least one `<option>`
- Provide 2-4 suggested options per question
- Options should be concise and distinct
- For multi-select questions (user can pick multiple options), you MUST add `type="checkbox"` attribute to the `<question>` element (e.g., `<question id="1" type="checkbox">`). This includes DoD questions.



## Agent Guidelines Checklist

[ ] Adhere to the project's coding standards.
[ ] Write comments in the project's customary natural language.
[ ] Follow the project's commit message style.
[ ] Break down tasks using the TDD approach: for each task, write the corresponding test cases before implementation. Ensure all unit tests pass upon the completion of each task.
[ ] Write robust, non-fragile tests: mock only at boundaries (external APIs, I/O), assert on behavior/outcomes not implementation details, avoid exact string matching on error messages.
