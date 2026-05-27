## Checklist

[ ] Read src/cafe/data/agents/developer/Nick.md to understand your role and native language
[ ] Read the development guide in .cafe/issues/test/plan/iteration_001/output.md
[ ] Read the requirements document .cafe/issues/test/spec/iteration_001/output.md
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
