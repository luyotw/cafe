## Checklist

[ ] Read src/cafe/data/agents/developer/Nick.md to understand your role and native language
[ ] Read the requirements specification .cafe/issues/test/spec/iteration_001/output.md
[ ] Read the implementation plan .cafe/issues/test/plan/iteration_001/output.md
[ ] Review all commits in the current branch
[ ] Edit .cafe/issues/test/pr/iteration_001/output.md to fill in PR title and description (NOT in your response)
[ ] Ensure PR title is concise and descriptive (max 80 characters)
[ ] Include reference to original requirements
[ ] List all major changes and commits in the Changes section
[ ] Do not query or wait for a remote GitHub branch/PR; host-side publish runs after this phase returns
[ ] Update blackboard and next-step baton to hand off to the next workflow target
[ ] Mark this checklist complete before returning confirmed


## Agent Guidelines Checklist

[ ] Adhere to the project's coding standards.
[ ] Write comments in the project's customary natural language.
[ ] Follow the project's commit message style.
[ ] Break down tasks using the TDD approach: for each task, write the corresponding test cases before implementation. Ensure all unit tests pass upon the completion of each task.
[ ] Write robust, non-fragile tests: mock only at boundaries (external APIs, I/O), assert on behavior/outcomes not implementation details, avoid exact string matching on error messages.
