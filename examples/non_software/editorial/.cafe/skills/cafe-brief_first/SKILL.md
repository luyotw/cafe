---
name: cafe-brief_first
description: Create the initial editorial brief.
version: 1.0.0
---

# Editorial Brief

## Role
Read your agent file: {agent_file}

## Instructions
- Turn the request into a crisp content brief with audience, angle, evidence requirements, and acceptance checks.
- If essential context is missing, write questions and hand off to `user` for clarification.
- When the brief is ready for approval, write the next-step baton to `user` for confirmation before drafting.

## Output
Write brief to: {output_file}

## Handoff
- 依照本輪結果更新 blackboard 與 next-step baton。
