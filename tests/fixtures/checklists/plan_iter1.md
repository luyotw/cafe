## Checklist

[ ] Read src/cafe/data/agents/developer/Nick.md to understand your role and native language
[ ] Read the initial development guide and preserve it verbatim under `## Development Guide` in .cafe/issues/test/plan/iteration_001/output.md
[ ] Read the requirements document .cafe/issues/test/spec/iteration_001/output.md
[ ] Inspect only the repository evidence needed to choose an implementation direction (planning, not implementation); do not draft the detailed Plan yet
[ ] Before recommending an unset runtime/deployment architecture, treat the user as non-technical by default: reuse existing evidence and ask only missing plain-language usage questions that materially change the direction
[ ] Confirm the recommendation does not assume a fixed IP, an always-on personal computer/NAS, self-managed server expertise, or authorization to adopt/pay for/deploy an external service
[ ] Write `<!-- plan-stage: solution-alignment -->` as the first non-blank line; ignore marker-looking text anywhere else
[ ] Write `Plan confirmation answer: <localized exact answer>` as the second non-blank line, using one concise answer in your native language; treat no other location as confirmation protocol data
[ ] Write `# Unconfirmed Solution Direction`, `Status: UNCONFIRMED — not executable`, and the sections **Recommended Direction**, **Will Do**, **Will Not Do**, and **Key Trade-offs**; use one recommendation, at most 3 scope items per side, at most 2 material tradeoffs, and explicit `None` when no tradeoff applies
[ ] Confirm the proposed scope is sufficient but not excessive: it covers the spec without speculative scope, unnecessary complexity, abstractions, or follow-on work
[ ] During solution alignment, do not write a Test List, implementation tasks, file-by-file steps, dependency ADR, or executable Plan content
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
- Provide 2-4 suggested options per question, except `solution_direction_confirmation`, which must provide exactly the single canonical confirmation option required by the Plan skill
- Options should be concise and distinct
- For multi-select questions (user can pick multiple options), you MUST add `type="checkbox"` attribute to the `<question>` element (e.g., `<question id="1" type="checkbox">`). This includes DoD questions.
- During `solution-alignment`, create exactly one question with id `solution_direction_confirmation`; its title must repeat the short Recommended Direction, Will Do, Will Not Do, and Key Trade-offs so the question is self-contained.
- Give that question exactly one explicit option whose parsed text is identical to the localized canonical `Plan confirmation answer`; do not add an Other option because the UI supplies free text.
- Hand off to `user` with `need_clarification`; do not route to `develop` or use `confirm_output` for this checkpoint.



## Agent Guidelines Checklist

[ ] Adhere to the project's coding standards.
[ ] Write comments in the project's customary natural language.
[ ] Follow the project's commit message style.
[ ] Break down tasks using the TDD approach: for each task, write the corresponding test cases before implementation. Ensure all unit tests pass upon the completion of each task.
[ ] Write robust, non-fragile tests: mock only at boundaries (external APIs, I/O), assert on behavior/outcomes not implementation details, avoid exact string matching on error messages.
