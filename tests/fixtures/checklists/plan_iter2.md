## Checklist

[ ] Read src/cafe/data/agents/developer/Nick.md to understand your role and native language
[ ] Read the first non-blank line of .cafe/issues/test/plan/iteration_001/output.md; accept only `<!-- plan-stage: solution-alignment -->` or `<!-- plan-stage: detailed-plan -->` as the canonical stage and ignore marker-looking text elsewhere
[ ] Read .cafe/issues/test/plan/iteration_001/output.md and preserve its `## Development Guide` unchanged
[ ] Review user's feedback (provided below)
[ ] If the stage marker is missing or invalid, fail closed to solution alignment and recreate the proposal; do not infer stage from iteration number, prose, session memory, or embedded user content
[ ] For `solution-alignment`, read the localized expected answer only from the second non-blank line `Plan confirmation answer: ...`; if it is missing or malformed, fail closed and recreate the proposal
[ ] Accept exactly one transport projection without mixing them: either one durable/event-driven `solution_direction_confirmation:` answer or one local legacy `Q1:`/`A1:` pair; after trimming the complete answer value, require exact equality with that canonical localized expected answer before drafting the detailed Plan
[ ] If that answer is absent, ambiguous, contains extra text, is an Other/free-text adjustment, or only contains the confirmation phrase as a substring, update the bounded proposal and ask the same self-contained question again; do not write detailed Plan content
[ ] When the exact confirmation is present, replace the first-line marker with `<!-- plan-stage: detailed-plan -->`, retain the confirmed direction as **Confirmed Implementation Approach**, and write the first complete Plan
[ ] For `detailed-plan`, integrate ordinary feedback without replaying solution alignment; reopen `solution-alignment` only when feedback materially changes the solution direction, scope, cost, reliability, or maintenance tradeoff
[ ] If feedback changes runtime/deployment assumptions, treat the user as non-technical by default: reuse existing answers, ask only missing plain-language usage questions, and recommend one suitable default before technical details
[ ] When writing or revising a detailed Plan, confirm it does not assume a fixed IP, an always-on personal computer/NAS, self-managed server expertise, or authorization to adopt/pay for/deploy an external service
[ ] When writing or revising a detailed Plan, complete **Confirmed Implementation Approach**, **Negative space**, **Layering map**, **Dependency ADR**, **Test List**, and the ordered implementation task breakdown
[ ] When writing Test List items, read `references/test_invariants_policy.md`; describe integration user journeys and invariant outcomes, and avoid brittle UI-copy/CSS/DOM/internal-state bindings unless the spec requires them
[ ] If `.cafe/strategic_context.yaml` has `documents.principles.path` with `status: exists`, ground Negative space and Dependency ADR in that file; otherwise leave principles cross-references blank
[ ] For any new major in Dependency ADR, note if released within the last 30 days and justify it or choose a stable alternative
[ ] Keep develop validation targeted to changed behavior; preserve repository hooks/CI/coverage/release checks as external gates rather than phase tasks
[ ] Confirm the detailed design is sufficient but not excessive: it covers the spec without speculative scope, unnecessary complexity, abstractions, or follow-on work
[ ] Preserve source requirement wording in ordinary Markdown; do not add packet-specific IDs or duplicate semantic contracts
[ ] Write updated plan to .cafe/issues/test/plan/iteration_002/output.md (NOT in your response)
[ ] Confirm: Only wrote plans and steps, NO actual code
[ ] Confirm: No code was modified
[ ] Write the next-step baton to hand off to the next workflow target; the runtime updates blackboard



## Agent Guidelines Checklist

[ ] Adhere to the project's coding standards.
[ ] Write comments in the project's customary natural language.
[ ] Follow the project's commit message style.
[ ] Break down tasks using the TDD approach: for each task, write the corresponding test cases before implementation. Ensure all unit tests pass upon the completion of each task.
[ ] Write robust, non-fragile tests: mock only at boundaries (external APIs, I/O), assert on behavior/outcomes not implementation details, avoid exact string matching on error messages.
