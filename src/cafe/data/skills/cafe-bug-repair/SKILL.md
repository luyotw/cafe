---
name: cafe-bug-repair
description: Minimal defect repair with unchanged RED/GREEN regression evidence
version: 1.1.0
workflow:
  execution_profile:
    workload: implementation
    reasoning: standard
    risk_domains:
    - correctness
    - integration
    fallback_strength: equivalent_or_stronger
  human_tasks:
  - id: clarification-feedback
    pattern: revision_feedback
    prompt:
      message_key: human_task.cafe_bug_repair.clarification_feedback.prompt
    prompt_locales:
      zh-TW:
        message_key: human_task.cafe_bug_repair.clarification_feedback.prompt
    input_schema: feedback
    correction_guidance:
      message_key: human_task.cafe_bug_repair.correction_guidance
    correction_guidance_locales:
      zh-TW:
        message_key: human_task.cafe_bug_repair.correction_guidance
  - id: permission-answers
    pattern: revision_feedback
    prompt:
      message_key: human_task.cafe_bug_repair.permission_answers.prompt
    prompt_locales:
      zh-TW:
        message_key: human_task.cafe_bug_repair.permission_answers.prompt
    input_schema: feedback
    correction_guidance:
      message_key: human_task.cafe_bug_repair.correction_guidance
    correction_guidance_locales:
      zh-TW:
        message_key: human_task.cafe_bug_repair.correction_guidance
  - id: iteration-limit
    pattern: confirm_output
    prompt:
      message_key: human_task.cafe_bug_repair.iteration_limit.prompt
    prompt_locales:
      zh-TW:
        message_key: human_task.cafe_bug_repair.iteration_limit.prompt
    input_schema: decision
    decisions:
    - id: resume
      label:
        message_key: human_task.cafe_bug_repair.iteration_limit.resume
      label_locales:
        zh-TW:
          message_key: human_task.cafe_bug_repair.iteration_limit.resume
    correction_guidance:
      message_key: human_task.cafe_bug_repair.correction_guidance
    correction_guidance_locales:
      zh-TW:
        message_key: human_task.cafe_bug_repair.correction_guidance
  prompt_inputs:
  - artifacts:
    - bug_diagnosis
    placeholder: diagnosis_file
    required: true
  - artifacts:
    - workspace
    placeholder: workspace_file
    required: true
  - artifacts:
    - code
    placeholder: prior_output_file
    required: false
  - artifacts:
    - workflow_feedback
    placeholder: workflow_feedback_file
    required: false
  - artifacts:
    - causal_todo
    placeholder: causal_todo_file
    required: false
  - artifacts:
    - review_feedback
    - pr_result
    placeholder: feedback_file
    required: false
  - artifacts:
    - review_feedback
    placeholder: review_feedback_file
    required: false
  - artifacts: [delivery_result]
    placeholder: delivery_feedback_file
    required: false
  prompt_references:
    evidence_template: evidence.md
  checklist:
    variants:
    - when:
        feedback: true
      sections:
      - reference: proof.md
      - reference: correction.md
      - todo_projection:
          artifact: causal_todo
          causal: true
    - when: {}
      sections:
      - reference: proof.md
    include_role_guidance: true
---

# Repair bounded defect

## Role
Read your agent file: {agent_file}

## Context
Use the current request, declared inputs and injected route catalog. Preserve the
confirmed defect boundary and repository requirements. Expected behavior comes
from confirmed decisions; a user report is evidence to verify.

## Instructions
- Read authoritative inputs once, then inspect only relevant source/tests with bounded output. Follow the shared workflow skill's repository inspection and quality-gate rules.
- Persist evidence and unfinished work to {output_file} after each bounded unit and continue all authorized executable work within this invocation.
- On interruption, first read the existing {output_file} checkpoint before overwriting it on a same-iteration retry, then the runtime delta packet previous_output and optional prior_output_file. Reuse evidence only when the unfixed revision, environment and exact regression identity still match; incomplete checks remain incomplete. Do not reconstruct streaming logs.
- Keep the configured execution chain and human authority boundaries. Preserve actual subprocess exit statuses; progress or an exit alone is not explicit phase completion.
- Missing reproduction, disputed expected behavior, broader investigation or scope decisions require questions in {questions_xml_file} and the existing user / need_clarification handoff. Record known facts and the missing decision. Missing permission uses user / need_permission. A response resumes this activity within existing scope and authority.
- Select all success and correction routes from the injected route catalog. An ordinary successful baton is {"version":1,"intent":"await_agent"}; a discretionary correction uses the declared target and manual_handoff. Never invent a route or bypass an independent responsibility.
- At an iteration-limit HumanTask, retain unresolved findings and evidence. Do not reset counts or automatically raise limits; only a human-authorized supported adjustment and response can resume.
- When consuming causal_todo, retain canonical item IDs, source fingerprints and closure/evidence requirements. Record progress in the phase output, never edit producer artifacts. A completed item requires a clean worktree including untracked files; Files lists at most 32 repo-relative tracked paths in backticks and Commit lists at most 8 full resolvable covering SHAs in backticks. With no repository changes use Files: N/A (no repository changes), Commit: N/A (no repository changes): <reason>.
- Finish all applicable checklist gates, write a non-empty summary with current evidence, then write the legal next-step baton. Never write the blackboard or execute GitHub sync wrappers.

- Read the required diagnosis and verified workspace. Install the exact replayable regression and establish RED before editing production behavior. Match the unfixed revision and assertion identity; changed assertions or baselines require renewed proof.
- Repair only the confirmed defect and directly necessary coverage. Run the unchanged regression to GREEN, then directly relevant existing checks. Commit the test and fix together through normal repository hooks; keep a clean workspace.
- Retain the original request, diagnosis references, unfixed revision/test identity, RED and GREEN commands/exit statuses, bounded failure/pass evidence, checks, diff scope and covering commits in the output so the existing PR consumer has a self-contained summary.
- If evidence invalidates diagnosis, select its declared correction route and identify precisely what needs replacing. Do not claim repaired HEAD reproduces the original defect; renewed RED runs against an identified unfixed baseline in isolation.
- Every successful repaired batch, including PR corrections, enters independent review. No no-change claim or manual shortcut can skip that responsibility.
- Follow the shared Develop and review disagreement protocol: at most three back-and-forth rounds for the same dispute, then existing clarification with both positions. This does not introduce another counter or replace runtime attempt bounds.

## Evidence format
{evidence_template}

## Output
Write the phase evidence and development/review summary to {output_file}.

## Handoff
Write {next_step_path} only after completion or a genuine existing human boundary.
