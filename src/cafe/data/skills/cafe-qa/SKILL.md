---
name: cafe-qa
description: Use this skill when a workflow needs independent black-box acceptance before PR publication.
version: 1.6.0
workflow:
  step_defaults:
    version: 1
    values:
      assignee_type: agent
      output_artifact: qa_feedback
      allowed_tools:
      - Read
      - Grep
      - Glob
      - Bash
      hooks:
        prepare_input:
        - UserInputCollector
      max_iterations: 5
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
  checklist:
    variants:
    - when: {}
      sections:
      - reference: execution_steps.md
      - reference: regression_acceptance.md
    include_role_guidance: true
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
- Before choosing evidence, identify the affected public entry, prerequisite state, promised result, and boundaries crossed by each applicable criterion. Select the corresponding App, public API, CLI, presentation, or document artifact entry; browser testing is required only where the affected behavior needs it.
- In the existing QA report's criteria table, record the entry and prerequisite state, substitutes and bypassed boundaries, actual observed result with evidence source, and coverage limits. Keep this within the same `qa_feedback`; a command name or PASS label alone does not establish the claimed outcome.
- Claims spanning App entry, session/authorization, or persistence boundaries need connected evidence through the affected sequence. Separate fixture UI and real SDK/Rules passes retain their local value but cannot automatically compose into a complete user journey PASS. Identify where each fixture starts and ends, what it pre-unlocks or seeds, and which production transitions it bypasses. For a local UI change, reuse unaffected persistence evidence when its source and relevant boundaries still match; do not demand a new full journey for every edit.
- Compare exercised data shapes with necessary branches of normal user operations, including cardinality, state, and payload differences that can change the result. Add risk-based representative scenarios for uncovered branches, rather than a fixed case count or all combinations. Code inspection can identify a missing branch but cannot prove its runtime result or a suspected cost/root cause; obtain observations before making those claims.
- Distinguish the confirmed stage's acceptance from completion of the full user outcome. Preserve explicitly deferred capabilities as incomplete without expanding the stage's scope or counting them as passed. A failure within the current commitment still requires correction, even in a staged delivery.
- Fill evidence gaps directly when the checks are executable within current QA scope. Route observed failures or required development of lasting regression coverage through the existing discretionary route marked `carries_feedback`, with reproducible evidence or the precise missing proof; missing evidence alone is not a proven product defect. Use the existing blocked/human route only for a genuine execution resource or permission blocker, and identify that blocker instead of relabeling an ordinary coverage gap as blocked.
- Complete `references/execution_steps.md` in the existing checklist: identify entries/substitutes, evidence applicable scenarios and route gaps, and keep conclusions within evidence. Checked gates prevent omitted procedure; they do not prove semantic adequacy or turn partial evidence into a pass.
- Before selecting acceptance commands and on every correction, complete `references/regression_acceptance.md` and its runtime checklist. Keep its Regression Coverage table in the QA report; shared-entry callers and existing tests remain in scope even when the latest correction changes only a few files.
- When the project explicitly adopts `verification.profile_file` and `verification.profile` in `.cafe/config.yaml`, use `cafe verification run --output-file {output_file}` and `cafe verification check --output-file {output_file}` as its shared repository-owned profile entry. Repeat any selected `--add-suite` values on run/check/reuse. The public adapter reads only the project binding; issue overrides cannot replace its mandatory suites. CI uses the same manifest/profile explicitly. A malformed binding is an actionable configuration error.
- For adopted profiles, record required/selected/completed/reused/pending suites, actual exits, receipt/log provenance and context applicability reasons. A partial targeted pass, `full` label, or stale receipt cannot satisfy missing mandatory coverage. Optional `run --reuse-from <source-output>` executes pending rows; `reuse` alone must validate every required row before materializing evidence. Retain independent black-box acceptance and semantic coverage judgment. See `docs/verification/profiles.md` for the public contract.
- Without explicit adoption, retain existing acceptance and receipt limitations; a missing profile or receipt creates no new mandatory stop. Develop/Review targeted checks, repository CI owners and confirmed postmerge/deployed-identity gates retain their existing responsibilities.
- Exercise every applicable acceptance criterion using observable scenarios or commands; do not infer a pass from code inspection alone.
- Do not modify product code. When acceptance fails, record reproducible evidence and select the injected discretionary route marked `carries_feedback`.
- When acceptance fails, emit at most 100 corrections under `## Todo List`, each once as ``- [ ] `QA-NNN` — Source: `qa` — Work: ... — Closure: ... — Evidence: ...``. Keep stable unique IDs; ordinary report checkboxes are not correction work. When no correction exists, write only the canonical marker `No actionable work.` in that section; never leave it blank.
- When a required check cannot run, use `need_clarification` or `need_permission` and resume in QA after the blocker is resolved.
- Record the criteria checked, scenarios or commands exercised, observed outcomes, blocked checks, and reproducible failure details in the QA report.
- Route a fully passing report to the playbook's next step.

## Output
Write QA report to: {output_file}
Include the criteria table (criterion/current-stage commitment, entry/prerequisite state, substitutes/bypassed boundaries, observed result/evidence source, coverage limits), the Regression Coverage table, current and reused evidence HEADs, unresolved failures, and any repository-owned checks still pending with their existing owner. State separately whether the confirmed stage's QA acceptance passed, whether the full user outcome is complete or has deferred capabilities, and whether full CI or delivery validation is still pending. For profile adoption include a suite evidence table (required/selected, completion/actual exit, executed/reused/pending, evidence HEAD/source/log, context applicability reason, owner). Record equal-coverage executed/reused counts and observed durations when comparing reuse; do not claim unmeasured speedups. Include escape observations for defects first discovered after QA and after merge: observation status (`known` or `unknown`), count only when known, source/window and unresolved owner. Unavailable observation is unknown, never zero.

## Handoff
- Write the next-step baton for this result; the runtime updates the blackboard.
