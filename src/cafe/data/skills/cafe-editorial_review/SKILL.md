---
name: cafe-editorial_review
description: Review editorial quality and alignment with the brief
version: 1.1.0
workflow:
  execution_profile:
    workload: review
    reasoning: standard
    risk_domains: [source-fidelity, audience-alignment]
    fallback_strength: equivalent_or_stronger
  prompt_inputs:
    - artifacts: [review_feedback]
      placeholder: correction_source
      required: false
---

# Editorial Review

## Role
Read your agent file: {agent_file}

## Instructions
Review the draft's clarity, support, and structure. If changes are needed, provide actionable revision directions. Keep every backward revision request in the canonical `## Todo List`; when no actionable revision exists, write only `No actionable work.`.

## Output
Write review to: {output_file}

## Handoff
- Write the next-step baton for this result; the runtime updates the blackboard.
