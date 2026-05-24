## Checklist

[ ] Read src/cafe/data/agents/developer/Nick.md to understand your role and native language
[ ] Carefully read .cafe/issues/test/spec/iteration_001/output.md and .cafe/issues/test/plan/iteration_001/output.md
[ ] Read feedback todo list in .cafe/issues/test/review/iteration_001/output.md
[ ] Address each issue raised in the feedback
[ ] Mark completed items in .cafe/issues/test/review/iteration_001/output.md if applicable (change - [ ] to - [x])
[ ] Commit changes with descriptive messages
[ ] Confirm: Maximized code reuse by looking for existing patterns and utilities
[ ] Confirm: Commit messages strictly match existing format, language, and structure
[ ] Confirm: All issues are fixed
[ ] Confirm: All tests pass and are not fragile
[ ] Update blackboard and next-step baton to hand off to the next workflow target

## Handoff Targets

- `review`: All issues fixed or you have written a technical dispute to .cafe/issues/test/develop/iteration_001/output.md
- `user`: The dispute needs user arbitration



## Agent Guidelines Checklist

[ ] Adhere to the project's coding standards.
[ ] Write comments in the project's customary natural language.
[ ] Follow the project's commit message style.
[ ] Break down tasks using the TDD approach: for each task, write the corresponding test cases before implementation. Ensure all unit tests pass upon the completion of each task.
[ ] Write robust, non-fragile tests: mock only at boundaries (external APIs, I/O), assert on behavior/outcomes not implementation details, avoid exact string matching on error messages.
