---
name: cafe-develop
description: Implement and test authorized workflow work from the current handoff, plan or corrective feedback.
version: 1.12.2
workflow:
  step_defaults:
    version: 1
    values:
      assignee_type: agent
      output_artifact: code
      allowed_tools:
      - Read
      - Edit
      - Write
      - Grep
      - Glob
      - Bash
      - WebFetch
      - WebSearch
      hooks:
        prepare_input:
        - UserInputCollector
        after_execute:
        - NoChangesNeededHandler
        - PermissionRetryHandler
  notification:
    step_label:
      message_key: notification.step_labels.develop
    task_labels:
      no-change-decision:
        message_key: notification.action_labels.no_changes_needed
      clarification-feedback:
        message_key: notification.action_labels.clarification_feedback
      permission-answers:
        message_key: notification.action_labels.permission_answers
  execution_profile:
    workload: implementation
    reasoning: standard
    risk_domains: [integration, state-change]
    fallback_strength: equivalent_or_stronger
  human_tasks:
    - id: no-change-decision
      pattern: no_changes_needed
      prompt: {message_key: human_task.cafe_develop.no_change_decision.prompt}
      prompt_locales:
        zh-TW: {message_key: human_task.cafe_develop.no_change_decision.prompt}
      input_schema: decision
      decisions:
        - id: agree
          label: {message_key: human_task.cafe_develop.no_change_decision.decisions.agree.label}
          label_locales:
            zh-TW: {message_key: human_task.cafe_develop.no_change_decision.decisions.agree.label}
        - id: disagree
          label: {message_key: human_task.cafe_develop.no_change_decision.decisions.disagree.label}
          label_locales:
            zh-TW: {message_key: human_task.cafe_develop.no_change_decision.decisions.disagree.label}
          requires_feedback: true
          correction: true
    - id: clarification-feedback
      pattern: revision_feedback
      prompt: {message_key: human_task.cafe_develop.clarification_feedback.prompt}
      prompt_locales:
        zh-TW: {message_key: human_task.cafe_develop.clarification_feedback.prompt}
      input_schema: feedback
    - id: permission-answers
      pattern: revision_feedback
      prompt: {message_key: human_task.cafe_develop.permission_answers.prompt}
      prompt_locales:
        zh-TW: {message_key: human_task.cafe_develop.permission_answers.prompt}
      input_schema: feedback
  prompt_inputs:
    - artifacts: [spec]
      placeholder: spec_file
      required: false
      load_policy:
        - when: {feedback: true}
          mode: packet
          contract_kind: spec
    - artifacts: [spec]
      placeholder: spec_file_path
      required: false
      load_policy:
        - when: {feedback: true}
          mode: packet
          contract_kind: spec
    - artifacts: [plan]
      placeholder: plan_file
      required: false
      load_policy:
        - when: {feedback: true}
          mode: packet
          contract_kind: plan
    - artifacts: [plan]
      placeholder: plan_file_path
      required: false
      load_policy:
        - when: {feedback: true}
          mode: packet
          contract_kind: plan
    - artifacts: [qa_feedback, review_feedback, pr_result, delivery_result]
      placeholder: feedback_file_path
      required: false
    - artifacts: [qa_feedback, review_feedback, pr_result, delivery_result]
      placeholder: feedback_file
      required: false
    - artifacts: [delivery_result]
      placeholder: delivery_feedback_file
      required: false
    - artifacts: [workflow_feedback]
      placeholder: workflow_feedback_file
      required: false
  checklist:
    context_references:
      normal_plan_context: normal_plan_context.md
      normal_plan_verification: normal_plan_verification.md
      correction_plan_context: correction_plan_context.md
      correction_plan_test_list: correction_plan_test_list.md
      xml_questions_instruction: xml_questions_instruction.md
    variants:
      - when: {feedback: true}
        sections:
          - reference: execution_steps_correction.md
          - todo_projection: {artifact: causal_todo, causal: true}
          - optional_checklist: basic_principles.md
      - when: {artifact_present: [plan]}
        sections:
          - reference: execution_steps_normal.md
          - todo_projection: {artifact: plan, source: plan}
          - optional_checklist: basic_principles.md
      - when: {}
        sections:
          - reference: execution_steps_normal.md
          - optional_checklist: basic_principles.md
    include_role_guidance: true
---

# Develop

## Role
Read your agent file: {agent_file}

## Context
- Use the workflow inputs listed in the runtime context. When a specification or plan is supplied, treat it as authoritative for this run.

## Instructions
- Complete the current authorized work from the supplied requirements and plan; when neither is supplied, use the current request and corrective feedback.
- Follow **First Steps** in `cafe-workflow-common` for fresh or resumed context recovery. Apply clarification answers to their matching questions and continue pending work; verify requested corrections against their active feedback. Do not restart discovery merely because the iteration changed.
- Make one bounded discovery pass for the current item: consult only missing requirements, Test List entries and feedback, then search the expected implementation points. Without new evidence, do not reread unchanged files or repeat the same search, `git status` or `git diff`.
- Add tests before changing behavior. Map new or changed tests to the supplied plan's **Test List**. When an accepted plan is supplied and scope changes, first use the workflow's planning route to update that plan before implementation; a clarification answer alone does not revise it. If no such route exists, request an authorized handoff rather than inventing one.
- Assert invariants rather than UI copy, CSS classes, DOM structure or internal state shape. Accessibility roles/labels, `data-testid` and specification-mandated copy are allowed; see `cafe-plan/references/test_invariants_policy.md`.
- Run bounded targeted checks for the changed behavior, mapped to the plan's Test List when supplied. Do not duplicate repository full-suite, coverage, release or pre-push gates in this phase.
- Keep this invocation running through every authorized executable item. A bounded unit, commit or targeted check is progress, not an iteration boundary or reason to hand off.
- After each bounded unit, validate evidence and immediately update `{output_file}` `## Todo Progress`, then continue. Record item ID, status, source fingerprint, files, commit (or an explicit reason for none), remaining work and next action. Accepted plan and feedback are immutable: never edit their checkboxes or Task Status.
- When a clarification answer arrives in the same session with usable context, apply it to the interrupted item and take its next authorized action. Read only what the answer requires or what resolves changed or missing context. Save the checklist sweep for completion. In a new or replacement session, recover the item and next action from the bounded blackboard handoff, existing progress ledger and relevant declared inputs.
- Recheck claims affected by new answers, changed sources, worktree or dependencies, or missing/stale evidence; retain unaffected evidence only while its source fingerprint and applicability still hold. A checked box or existing commit alone is not completion, and Git evidence alone does not establish test-result applicability.
- Use exactly `### <item ID>` as each ledger entry's heading, without a title suffix. For each completed item, list at most 32 backtick-quoted repo-relative tracked paths in `Files` and 8 resolvable full backtick-quoted SHAs covering those files in `Commit`. With no repository changes, use `Files: N/A (no repository changes)` and `Commit: N/A (no repository changes): <reason>`. Completion requires a clean worktree, including untracked files. Never invent Files/Commit or replace them with free text. `Targeted evidence` is optional informational prose.
- Retain a checklist `[x]` only when the current Todo source fingerprint and ledger evidence still match; similar wording, an old commit or an upstream checkbox cannot replace evidence.
- End only when all applicable work and checklist gates are complete with a valid handoff; a real clarification, permission or arbitration requires a valid handoff; or the provider/tool cannot continue. In the last case preserve a truthful checkpoint without inventing completion, questions or a baton. A progress-only reply or request for another `continue`/`resume` is not a valid stopping point.
- Before a blocking clarification, checkpoint the blocked item, its next action, and any new progress in `{output_file}`, then write the question and clarification baton. Leave final checklist and completion checks for the successful handoff.
- For other handoffs, write a non-empty development summary to `{output_file}`. Focus on new changes, decisions and remaining work; preserve relevant earlier evidence without retelling the full history.
- Reuse existing patterns and tools; preserve repository commit style and code-comment language.
- Follow `cafe-workflow-common` **Bounded repository inspection**, **Repository-owned quality gates**, **Develop and review disagreement protocol** and **Shared Rules** for inspection limits, gate ownership, disputes and handoff.

## Output
Write development summary to: {output_file}

## Handoff
Write next-step baton for this result; the runtime updates the blackboard.

For a one-time staged commit permission, save `staged_commit_request.json` beside the current output with a single-line `message` and boolean `skip_hooks`. Set `skip_hooks` to true only when explicitly requesting an exception for this commit. Submit the existing permission handoff and leave the staged state intact. At continuation use the runtime-supplied commit command.
