## Checklist

[ ] Read src/cafe/data/agents/developer/Nick.md to understand your role and native language
[ ] Read .cafe/issues/test/pr/iteration_001/output.md to review previous PR content
[ ] Review unpushed commits to identify new changes
[ ] Edit .cafe/issues/test/pr/iteration_002/output.md to update PR content based on new changes (NOT in your response)
[ ] Refresh the required `Follow-up Proposals` section without renaming, dropping, or inventing open `FUP-NNN` entries; write `None` when there are no open proposals
[ ] Do not query or wait for a remote GitHub branch/PR; host-side publish runs after this phase returns
[ ] Write the next-step baton to hand off to the next workflow target; the runtime updates blackboard
[ ] Mark this checklist complete before returning confirmed


## Agent Guidelines Checklist

[ ] Adhere to the project's coding standards.
[ ] Write comments in the project's customary natural language.
[ ] Follow the project's commit message style.
[ ] Break down tasks using the TDD approach: for each task, write the corresponding test cases before implementation. Ensure all unit tests pass upon the completion of each task.
[ ] Write robust, non-fragile tests: mock only at boundaries (external APIs, I/O), assert on behavior/outcomes not implementation details, avoid exact string matching on error messages.
