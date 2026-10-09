---
name: cafe-bug-diagnose
description: Verified diagnosis and replayable failing regression for a bounded defect
version: 1.0.0
workflow:
  execution_profile:
    workload: requirements
    reasoning: standard
    risk_domains:
    - correctness
    - integration
    fallback_strength: equivalent_or_stronger
  human_tasks:
  - id: clarification-feedback
    pattern: revision_feedback
    prompt:
      message_key: human_task.cafe_bug_diagnose.clarification_feedback.prompt
    prompt_locales:
      zh-TW:
        message_key: human_task.cafe_bug_diagnose.clarification_feedback.prompt
    input_schema: feedback
    correction_guidance:
      message_key: human_task.cafe_bug_diagnose.correction_guidance
    correction_guidance_locales:
      zh-TW:
        message_key: human_task.cafe_bug_diagnose.correction_guidance
  - id: permission-answers
    pattern: revision_feedback
    prompt:
      message_key: human_task.cafe_bug_diagnose.permission_answers.prompt
    prompt_locales:
      zh-TW:
        message_key: human_task.cafe_bug_diagnose.permission_answers.prompt
    input_schema: feedback
    correction_guidance:
      message_key: human_task.cafe_bug_diagnose.correction_guidance
    correction_guidance_locales:
      zh-TW:
        message_key: human_task.cafe_bug_diagnose.correction_guidance
  - id: iteration-limit
    pattern: confirm_output
    prompt:
      message_key: human_task.cafe_bug_diagnose.iteration_limit.prompt
    prompt_locales:
      zh-TW:
        message_key: human_task.cafe_bug_diagnose.iteration_limit.prompt
    input_schema: decision
    decisions:
    - id: resume
      label:
        message_key: human_task.cafe_bug_diagnose.iteration_limit.resume
      label_locales:
        zh-TW:
          message_key: human_task.cafe_bug_diagnose.iteration_limit.resume
    correction_guidance:
      message_key: human_task.cafe_bug_diagnose.correction_guidance
    correction_guidance_locales:
      zh-TW:
        message_key: human_task.cafe_bug_diagnose.correction_guidance
  prompt_inputs:
  - artifacts:
    - bug_diagnosis
    placeholder: diagnosis_file
    required: false
  - artifacts:
    - workspace
    placeholder: workspace_file
    required: false
  - artifacts:
    - code
    placeholder: code_file
    required: false
  - artifacts:
    - bug_diagnosis
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
    - code
    placeholder: feedback_file
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

# Diagnose bounded defect

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

- Diagnose before changing defective behavior. Record trigger, expected/observed behavior, reproduction environment, supported cause, facts versus uncertainty and proposed minimal repair scope.
- Obtain actual RED on the explicitly identified unfixed revision for the reported defect. Distinguish assertion failure from setup, dependency or unrelated failures. A passing reproduction or post-fix-only test is inconclusive and must not advance.
- Reuse a suitable existing test, or run the proposed regression in an isolated temporary checkout/copy of unfixed behavior. Preserve user changes and the active worktree; never reset it or bypass hooks to obtain RED. Retain complete runnable test source or a durable patch, exact identity/hash, command, actual exit status and bounded failure evidence.
- Hand off a clean active workspace without an intentionally failing committed test. The receiving repair activity installs the exact retained test and demonstrates RED before production edits. No routine diagnosis approval is added for verified in-scope proof.

## Evidence format
{evidence_template}

## Output
Write the phase evidence and development/review summary to {output_file}.

## Handoff
Write {next_step_path} only after completion or a genuine existing human boundary.
