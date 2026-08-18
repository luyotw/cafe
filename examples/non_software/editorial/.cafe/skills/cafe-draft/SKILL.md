---
name: cafe-draft
description: Produce the article draft.
version: 1.0.0
---

# Draft Article

## Role
Read your agent file: {agent_file}

## Instructions
- Write the article from the approved brief.
- Keep the structure readable, concrete, and audience-focused.
- If the brief is ambiguous enough to change scope or angle, write questions and hand off to `user` for clarification.
- When the draft is complete, write the next-step baton to the review step.

## Output
Write draft to: {output_file}

## Handoff
- 依照本輪結果更新 blackboard 與 next-step baton。
