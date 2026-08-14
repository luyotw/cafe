## Checklist

[ ] Read {agent_file} to understand your role and native language
[ ] Read {user_input_file} which contains the original PR review comments
[ ] Check {prev_output_file} (if exists) - skip todo items that are already completed there
[ ] Group related comments together
[ ] Convert comments into actionable todo list items (markdown checkbox format: - [ ] Item - keep all items UNCHECKED as they represent work to be done)
[ ] Write ONLY the organized todo list to {output_file} (do NOT copy original PR comments)
[ ] Write the next-step baton to hand off to `develop`; the runtime updates blackboard
