## Checklist

[ ] Read {agent_file} to understand your role and native language
{correction_plan_context}[ ] Read feedback todo list in {feedback_file}
[ ] Address each issue raised in the feedback
[ ] Mark completed items in {feedback_file} if applicable (change - [ ] to - [x])
[ ] Commit changes with descriptive messages
[ ] Confirm: Maximized code reuse by looking for existing patterns and utilities
[ ] Confirm: Commit messages strictly match existing format, language, and structure
[ ] Confirm: All issues are fixed
{correction_plan_test_list}[ ] Confirm: Corrected tests do not reintroduce brittle bindings (UI copy, CSS classes, DOM structure, internal state shape) unless the spec explicitly allows them
[ ] Confirm: All correction commits are made and the worktree is clean, then run the final repository-defined full test command exactly once through `cafe verification run --output-file {output_file} --scope full -- <command>`
[ ] Confirm: `cafe verification run` reported a valid receipt; do not change HEAD or tracked files afterward
[ ] Confirm: All tests pass and are not fragile
[ ] Update blackboard and next-step baton to hand off to the next workflow target

## Handoff Targets

- `review`: All issues fixed or you have written a technical dispute to {output_file}
- `user`: The dispute needs user arbitration
{xml_questions_instruction}
