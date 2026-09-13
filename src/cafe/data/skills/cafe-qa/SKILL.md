---
name: cafe-qa
description: Use this skill when a workflow needs independent black-box acceptance before PR publication.
version: 1.3.0
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
    - id: iteration-limit
      pattern: confirm_output
      prompt: The workflow reached its configured iteration limit. Increase the issue's limit if another acceptance check is authorized, then resume this phase.
      input_schema: decision
      decisions:
        - id: resume
          label: Resume after increasing the iteration limit
  prompt_inputs:
    - artifacts: [spec, requirements]
      placeholder: requirements_file
      required: true
    - artifacts: [code]
      placeholder: develop_file
      required: true
    - artifacts: [plan]
      placeholder: plan_file
      required: false
    - artifacts: [review_feedback]
      placeholder: review_file
      required: false
  prompt_references:
    optional_plan_context: optional_plan_context.md
    optional_review_context: optional_review_context.md
---

# QA

## Role
Read your agent file: {agent_file}

## Context
- Requirements Source: {requirements_file}
- Development Summary: {develop_file}
{optional_plan_context}
{optional_review_context}

## Instructions
- Perform black-box acceptance against the requested behavior recorded in the requirements source; treat that source as authoritative whether it is a formal specification or the immutable initial request.
- When an implementation plan is provided, exercise its Test List; otherwise derive observable scenarios from the available requirements and acceptance evidence.
- When a review result is provided, prioritize its identified risks and confirm that unresolved findings do not escape acceptance.
- Exercise every applicable acceptance criterion using observable scenarios or commands; do not infer a pass from code inspection alone.
- Do not modify product code. When acceptance fails, record reproducible evidence and route the work to `develop`.
- When acceptance fails, emit at most 100 corrections under `## Todo List`, each once as ``- [ ] `QA-NNN` — Source: `qa` — Work: ... — Closure: ... — Evidence: ...``. Keep stable unique IDs; ordinary report checkboxes are not correction work. When no correction exists, write only the canonical marker `No actionable work.` in that section; never leave it blank.
- When a required check cannot run, use `need_clarification` or `need_permission` and resume in QA after the blocker is resolved.
- Record the criteria checked, scenarios or commands exercised, observed outcomes, blocked checks, and reproducible failure details in the QA report.
- Route a fully passing report to the playbook's next step.

## Output
Write QA report to: {output_file}

## Handoff
- 依照本輪結果寫入 next-step baton；blackboard 由 runtime 更新。
