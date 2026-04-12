## Checklist

[ ] Read {agent_file} to understand your role and native language
[ ] Carefully read {spec_file_path} and {plan_file_path}
[ ] Read feedback todo list in {feedback_file_path}
[ ] Address each issue raised in the feedback
[ ] Mark completed items in {feedback_file_path} if applicable (change - [ ] to - [x])
[ ] Commit changes with descriptive messages
[ ] Confirm: Maximized code reuse by looking for existing patterns and utilities
[ ] Confirm: Commit messages strictly match existing format, language, and structure
[ ] Confirm: All issues are fixed
[ ] Confirm: All tests pass and are not fragile
[ ] Return status code

## Status Codes

- CAFE_CONFIRMED: All issues fixed, ready for review
- CAFE_NO_CHANGES_NEEDED: You believe reviewer's feedback is incorrect/unnecessary. Write your reasoning to {output_file} then return this code.
{xml_questions_instruction}
