---
name: cafe-chat-develop-change
description: "Classify any implementation change requested inside cafe chat, complete bounded low-risk work directly, and route broad or high-risk work to the correct full CAFE phase"
version: 1.2.0
---

# Chat Develop Change

## Use This Skill When
- The user asks for any implementation or code change during `cafe chat`.
- The request may be bounded and low-risk, or may introduce broad product, architecture, data, security, deployment, or external-state impact that must be routed elsewhere.

## Instructions
- Classify scope before editing code. Do not start with an implementation draft.
- Keep the change in chat only when it is bounded, reversible, and does not materially alter confirmed requirements or architecture.
- Stop direct development and route the request to the full CAFE workflow when it introduces any of the following:
  - a new product capability or user journey outside the confirmed spec or plan;
  - authentication, authorization, privacy, security, or data-ownership behavior;
  - a database schema change, data migration, or compatibility requirement;
  - deployment, infrastructure, paid-service, or other external-state impact;
  - a cross-module redesign or other change whose acceptance criteria need user confirmation.
- When full workflow is required, leave source files unchanged, explain the user-visible reason in plain language, and mark the chat outcome as follow-up needed. When uncertain, prefer the full workflow.
- Select the earliest responsible step from the active playbook whose artifact or decision is missing or stale:
  - the step that owns requirements, a brief, research question, user journey, trust boundary, or acceptance criteria when those expectations are unconfirmed;
  - the step that owns planning, an approach, architecture, migration, deployment, or risk controls when expectations are confirmed but execution is not safely planned;
  - the step that owns implementation or execution when upstream artifacts already cover the work but direct chat execution is too broad or high-risk.
- Inspect the active playbook before routing and use only a valid step name declared there. Do not assume generic phase names exist and do not invent a phase. If no agent step can responsibly realign the workflow, route to the built-in `user` step and explain what decision is missing.
- Apply `cafe-common-chat-handoff` for commit ordering and all blackboard, baton, and closing mechanics.
- Make the requested implementation change when it is appropriate to do so in chat.
- Be explicit about whether the code change was completed in chat or whether follow-up work is still needed.
- If the change affects broader workflow expectations, say that clearly in the closing handoff.
- Finish with the required common chat handoff format.
