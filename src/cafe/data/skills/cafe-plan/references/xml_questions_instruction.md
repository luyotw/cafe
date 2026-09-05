
## Interactive Q&A Questions

[ ] If user clarification is needed: write questions to {questions_xml_file} in the following XML format and hand off to `user` with need_clarification:

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
