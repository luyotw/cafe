---
name: cafe-qa
description: Use this skill when a workflow needs independent black-box acceptance before PR publication.
version: 1.0.0
workflow:
  execution_profile:
    workload: review
    reasoning: high
    risk_domains: [correctness, acceptance]
    fallback_strength: equivalent_or_stronger
  required_tools:
    - Bash
  human_tasks:
    - id: clarification-feedback
      pattern: revision_feedback
      prompt: Provide the information or permission needed to complete the blocked acceptance check.
      input_schema: feedback
  prompt_inputs:
    - artifacts: [spec]
      placeholder: spec_file
      required: true
    - artifacts: [plan]
      placeholder: plan_file
      required: true
    - artifacts: [code]
      placeholder: develop_file
      required: true
    - artifacts: [review_feedback]
      placeholder: review_file
      required: true
---

# QA

## Role
Read your agent file: {agent_file}

## Context
- Requirements Specification: {spec_file}
- Implementation Plan: {plan_file}
- Development Summary: {develop_file}
- Review Result: {review_file}

## Instructions
- Perform black-box acceptance against the confirmed requirements and the plan's Test List.
- Exercise every applicable acceptance criterion using observable scenarios or commands; do not infer a pass from code inspection alone.
- Do not modify product code. When acceptance fails, record reproducible evidence and route the work to `develop`.
- When a required check cannot run, use `need_clarification` or `need_permission` and resume in QA after the blocker is resolved.
- Record the criteria checked, scenarios or commands exercised, observed outcomes, blocked checks, and reproducible failure details in the QA report.
- Route a fully passing report to the playbook's next step.

## Output
Write QA report to: {output_file}

## Handoff
- 依照本輪結果寫入 next-step baton；blackboard 由 runtime 更新。
