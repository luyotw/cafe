## Checklist

[ ] Read {agent_file} to understand your role and native language
[ ] Read {previous_output_file} to review previous plan
[ ] Review user's feedback (provided below)
[ ] If feedback changes runtime/deployment assumptions, treat the user as non-technical by default: reuse existing answers, ask only missing plain-language usage questions, and recommend one suitable default before technical details
[ ] Confirm the revised plan does not assume a fixed IP, an always-on personal computer/NAS, self-managed server expertise, or authorization to adopt/pay for/deploy an external service
[ ] Integrate feedback and update the plan, DO NOT hint the existence of the previous iterations
[ ] Confirm the revised design remains the smallest design that satisfies the requirements, with no speculative scope or abstractions
[ ] Preserve source requirement wording in ordinary Markdown; do not add packet-specific IDs or duplicate semantic contracts
[ ] Keep **Negative space**, **Layering map**, and **Dependency ADR** filled and consistent with the revised plan (explicit "none" if still applicable)
[ ] Write updated plan to {output_file} (NOT in your response)
[ ] Keep "## Development Guide" section unchanged
[ ] Confirm: Only wrote plans and steps, NO actual code
[ ] Confirm: No code was modified
[ ] Write the next-step baton to hand off to the next workflow target; the runtime updates blackboard
{xml_questions_instruction}
