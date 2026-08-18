---
name: cafe-editorial_review
description: Review the article for quality and alignment.
version: 1.0.0
---

# Editorial Review

## Role
Read your agent file: {agent_file}

## Instructions
- Review the draft for clarity, factual grounding, structure, and fit to the brief.
- Prioritize concrete risks: unclear claims, unsupported evidence, weak structure, missed audience needs, and drift from the brief.
- If revisions are needed, write actionable feedback and route the next-step baton to the draft step.
- If the draft is ready, write the next-step baton to the publish step.

## Output
Write review to: {output_file}

## Handoff
- 依照本輪結果更新 blackboard 與 next-step baton。
