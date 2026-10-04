---
name: cafe-qa
description: Use this skill when a workflow needs independent black-box acceptance before PR publication.
version: 1.4.0
workflow:
  notification:
    task_labels:
      clarification-feedback:
        message_key: notification.action_labels.clarification_feedback
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
      prompt: {message_key: human_task.cafe_qa.clarification_feedback.prompt}
      prompt_locales:
        zh-TW: {message_key: human_task.cafe_qa.clarification_feedback.prompt}
      input_schema: feedback
    - id: iteration-limit
      pattern: confirm_output
      prompt: {message_key: human_task.cafe_qa.iteration_limit.prompt}
      prompt_locales:
        zh-TW: {message_key: human_task.cafe_qa.iteration_limit.prompt}
      input_schema: decision
      decisions:
        - id: resume
          label: {message_key: human_task.cafe_qa.iteration_limit.decisions.resume.label}
          label_locales:
            zh-TW: {message_key: human_task.cafe_qa.iteration_limit.decisions.resume.label}
  prompt_inputs:
    - artifacts: [spec]
      placeholder: spec_file
      required: false
    - artifacts: [code]
      placeholder: develop_file
      required: true
    - artifacts: [workspace]
      placeholder: workspace_file
      required: false
    - artifacts: [plan]
      placeholder: plan_file
      required: false
    - artifacts: [review_feedback]
      placeholder: review_file
      required: false
  prompt_references:
    optional_spec_context: optional_spec_context.md
    optional_plan_context: optional_plan_context.md
    optional_review_context: optional_review_context.md
---

# QA

## Role
Read your agent file: {agent_file}

## Context
{optional_spec_context}
- Development Summary: {develop_file}
{optional_plan_context}
{optional_review_context}

## Instructions
- Perform black-box acceptance against the requested behavior. When a requirements specification is provided, treat it as the acceptance source of truth.
- When a separate specification is absent and the supplied plan contains a complete `Requirements Specification` section, use that section as the acceptance source of truth and exercise its criteria alongside the plan's Test List.
- When neither source provides requirements, derive the behavior from the development summary and verify it against the changed product.
- When `workspace_file` is supplied, verify its Git head and changed-file set before accepting the development summary as current.
- When an implementation plan is provided, exercise its Test List; otherwise derive observable scenarios from the available requirements and acceptance evidence.
- When a review result is provided, prioritize its identified risks and confirm that unresolved findings do not escape acceptance.
- Exercise every applicable acceptance criterion using observable scenarios or commands; do not infer a pass from code inspection alone.
- Do not modify product code. When acceptance fails, record reproducible evidence and select the injected discretionary route marked `carries_feedback`.
- When acceptance fails, emit at most 100 corrections under `## Todo List`, each once as ``- [ ] `QA-NNN` — Source: `qa` — Work: ... — Closure: ... — Evidence: ...``. Keep stable unique IDs; ordinary report checkboxes are not correction work. When no correction exists, write only the canonical marker `No actionable work.` in that section; never leave it blank.
- When a required check cannot run, use `need_clarification` or `need_permission` and resume in QA after the blocker is resolved.
- Record the criteria checked, scenarios or commands exercised, observed outcomes, blocked checks, and reproducible failure details in the QA report.
- Route a fully passing report to the playbook's next step.

## Output
Write QA report to: {output_file}

## Handoff
- Write the next-step baton for this result; the runtime updates the blackboard.
