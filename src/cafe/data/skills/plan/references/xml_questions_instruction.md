
## Interactive Q&A Questions

[ ] If user clarification is needed: write questions to {questions_xml_file} in the following XML format and hand off to `user` with CAFE_NEED_CLARIFICATION:

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
