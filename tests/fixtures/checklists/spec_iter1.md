## Checklist

[ ] Read src/cafe/data/agents/pm/Roger.md to understand your role and native language
[ ] Read .cafe/issues/test/spec/iteration_001/output.md to understand initial requirements
[ ] If images exist in spec/images/, read and analyze them for UI/UX requirements and visual context
[ ] Read README.md for project context
[ ] Search codebase using Read/Grep tools to find answers before asking users
[ ] Identify unclear areas that need clarification
[ ] Preserve original requirements content - Keep the "Initial Requirements" section and "Issue Title" (if present) unchanged, only add your analysis below
[ ] Write analysis results to .cafe/issues/test/spec/iteration_001/output.md (NOT in your response)
[ ] Update blackboard and next-step baton to hand off to the next workflow target
[ ] Confirm: No technical details were included (no implementation, architecture, languages, frameworks, databases)
[ ] Confirm: Only provided 2-3 high-level approach options if any, without prescribing specific technical solutions
[ ] Confirm: No code was modified
[ ] Keep the response brief; workflow transitions are controlled by the baton

## Interactive Q&A Questions

[ ] If user clarification is needed: write questions to .cafe/issues/test/spec/iteration_001/questions.xml in the following XML format and hand off to `user` with `intent=need_clarification`:

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




## Definition of Done (DoD) -- MANDATORY

[ ] You MUST include DoD questions in questions.xml and request clarification (even if requirements are already clear -- send DoD questions alone)
[ ] DoD questions focus on functional requirements only (e.g., all major features working, error handling working, edge cases tested)
[ ] Do NOT add "Other" or custom input options to checkbox questions -- the system automatically adds an "Other" option to every checkbox question
[ ] NEVER mark the spec as ready for review without first confirming DoD with the user
[ ] After receiving user's DoD answers, integrate selected items into the Acceptance Criteria section with "✅ **DoD:**" prefix

## Agent Guidelines Checklist

[ ] Focus on the Requirement: Do not jump into discussions about technical implementation.
[ ] User Perspective: Think about functions and scenarios from the user's point of view.
[ ] Clear Communication: Ask questions in a simple and direct manner.
