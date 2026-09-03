## Checklist

[ ] Read {agent_file} to understand your role and native language
{spec_read_instruction}{plan_read_instruction}[ ] Review all commits in the current branch
[ ] Edit {output_file} to fill in PR title and description (NOT in your response)
[ ] Ensure PR title is concise and descriptive (max 80 characters)
[ ] Include reference to original requirements
[ ] List all major changes and commits in the Changes section
{review_feedback_instruction}[ ] Write the required `Follow-up Proposals` section, preserving every open `FUP-NNN` ID and writing `None` when there are no open proposals
[ ] Do not query or wait for a remote GitHub branch/PR; host-side publish runs after this phase returns
[ ] Write the next-step baton to hand off to the next workflow target; the runtime updates blackboard
[ ] Mark this checklist complete before returning confirmed
