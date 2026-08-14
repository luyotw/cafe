## Checklist

[ ] Read src/cafe/data/agents/developer/Nick.md to understand your role and native language
[ ] Read .cafe/issues/test/plan/iteration_001/output.md to review previous plan
[ ] Review user's feedback (provided below)
[ ] If feedback changes runtime/deployment assumptions, treat the user as non-technical by default: reuse existing answers, ask only missing plain-language usage questions, and recommend one suitable default before technical details
[ ] Confirm the revised plan does not assume a fixed IP, an always-on personal computer/NAS, self-managed server expertise, or authorization to adopt/pay for/deploy an external service
[ ] Integrate feedback and update the plan, DO NOT hint the existence of the previous iterations
[ ] Preserve source requirement wording without copying spec stable ID tokens (`GOAL-*`, `NONGOAL-*`, `AC-*`, `TRUST-*`); map each requirement to plan-owned `ARCH-*`, `INV-*`, `UT-*`, `IT-*`, `ADR-*`, or `TASK-*` IDs
[ ] Keep **Negative space**, **Layering map**, and **Dependency ADR** filled and consistent with the revised plan (explicit "none" if still applicable)
[ ] Write updated plan to .cafe/issues/test/plan/iteration_002/output.md (NOT in your response)
[ ] Keep "## Development Guide" section unchanged
[ ] Confirm: Only wrote plans and steps, NO actual code
[ ] Confirm: No code was modified
[ ] Write the next-step baton to hand off to the next workflow target; the runtime updates blackboard



## Agent Guidelines Checklist

[ ] Adhere to the project's coding standards.
[ ] Write comments in the project's customary natural language.
[ ] Follow the project's commit message style.
[ ] Break down tasks using the TDD approach: for each task, write the corresponding test cases before implementation. Ensure all unit tests pass upon the completion of each task.
[ ] Write robust, non-fragile tests: mock only at boundaries (external APIs, I/O), assert on behavior/outcomes not implementation details, avoid exact string matching on error messages.
