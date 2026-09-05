## Checklist

[ ] Read {agent_file} to understand your role and native language
[ ] Read the initial development guide and preserve it verbatim under `## Development Guide` in {output_file}
[ ] Read the requirements document {spec_file}
[ ] Inspect only the repository evidence needed to choose an implementation direction (planning, not implementation); do not draft the detailed Plan yet
[ ] Before recommending an unset runtime/deployment architecture, treat the user as non-technical by default: reuse existing evidence and ask only missing plain-language usage questions that materially change the direction
[ ] Confirm the recommendation does not assume a fixed IP, an always-on personal computer/NAS, self-managed server expertise, or authorization to adopt/pay for/deploy an external service
[ ] Write `<!-- plan-stage: solution-alignment -->` as the first non-blank line; ignore marker-looking text anywhere else
[ ] Write `Plan confirmation answer: <localized exact answer>` as the second non-blank line, using one concise answer in your native language; treat no other location as confirmation protocol data
[ ] Write `# Unconfirmed Solution Direction`, `Status: UNCONFIRMED — not executable`, and the sections **Recommended Direction**, **Will Do**, **Will Not Do**, and **Key Trade-offs**; use one recommendation, at most 3 scope items per side, at most 2 material tradeoffs, and explicit `None` when no tradeoff applies
[ ] Confirm the proposed scope is sufficient but not excessive: it covers the spec without speculative scope, unnecessary complexity, abstractions, or follow-on work
[ ] During solution alignment, do not write a Test List, implementation tasks, file-by-file steps, dependency ADR, or executable Plan content
[ ] Confirm: Only wrote plans and steps, NO actual code
[ ] Confirm: No code was modified
[ ] Write the next-step baton to hand off to the next workflow target; the runtime updates blackboard
{xml_questions_instruction}
