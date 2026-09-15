---
name: cafe-review
description: "Review code quality, behavior, and risk"
version: 1.13.0
workflow:
  execution_profile:
    workload: review
    reasoning: high
    risk_domains: [correctness, security]
    fallback_strength: equivalent_or_stronger
  human_tasks:
    - id: clarification-feedback
      pattern: revision_feedback
      prompt: Provide the clarification needed to continue the review.
      input_schema: feedback
    - id: iteration-limit
      pattern: confirm_output
      prompt: The workflow reached its configured iteration limit. Increase the issue's limit if another review is authorized, then resume this phase.
      input_schema: decision
      decisions:
        - id: resume
          label: Resume after increasing the iteration limit
  prompt_inputs:
    - artifacts: [spec]
      placeholder: spec_file
      required: false
      load_policy:
        - mode: packet
          contract_kind: spec
    - artifacts: [spec]
      placeholder: spec_file_path
      required: false
      load_policy:
        - mode: packet
          contract_kind: spec
    - artifacts: [plan]
      placeholder: plan_file
      required: false
      load_policy:
        - mode: packet
          contract_kind: plan
    - artifacts: [plan]
      placeholder: plan_file_path
      required: false
      load_policy:
        - mode: packet
          contract_kind: plan
    - artifacts: [code]
      placeholder: develop_file
      required: false
    - artifacts: [workspace]
      placeholder: workspace_file
      required: false
    - artifacts: [qa_feedback, review_feedback, pr_result]
      placeholder: feedback_file
      required: false
    - artifacts: [workflow_feedback]
      placeholder: workflow_feedback_file
      required: false
  checklist:
    context_references:
      spec_read_instruction: spec_read_instruction.md
      plan_read_instruction: plan_read_instruction.md
      feedback_instruction: feedback_instruction.md
      spec_comparison_instruction: spec_comparison_instruction.md
    variants:
      - when: {iteration: 1}
        sections:
          - reference: execution_preflight.md
          - reference: execution_risk_assessment.md
          - reference: execution_first_pass.md
          - reference: execution_acceptance_closure.md
          - reference: execution_exit_audit.md
          - reference: execution_finalize.md
          - optional_checklist: basic_principles.md
      - when: {min_iteration: 2, max_iteration: 3}
        sections:
          - reference: execution_preflight.md
          - reference: execution_correction.md
          - reference: execution_risk_assessment.md
          - reference: execution_acceptance_closure.md
          - reference: execution_exit_audit.md
          - reference: execution_finalize.md
          - optional_checklist: basic_principles.md
      - when: {min_iteration: 4}
        sections:
          - reference: execution_preflight.md
          - reference: execution_convergence.md
          - reference: execution_risk_assessment.md
          - reference: execution_acceptance_closure.md
          - reference: execution_exit_audit.md
          - reference: execution_finalize.md
          - optional_checklist: basic_principles.md
    include_role_guidance: true
---

# Review

## Role
Read your agent file: {agent_file}

## Context
- Use the workflow inputs listed in the runtime context. Review every supplied requirement, plan, implementation artifact, and feedback item that applies to this run.
- When `workspace_file` is supplied, verify the declared Git workspace companion before relying on the code summary; use it for changed-file and receipt identity, while treating `develop_file` as the human-readable development summary.

## Available scripts
- `scripts/update_review_fallback.py` — maintainer-only updater for the pinned open-source review procedure; never run it during workflow execution.

    python scripts/update_review_fallback.py --help

## Instructions
- Focus on defects and risk.
- First confirm that the supplied requirements, plan, and implementation agree.
- Use the authoring-time confirmed review discovery matrix: existing Codex and Claude reviewers are host-side CLI commands, not composable native Skills; no equivalent native Skills are confirmed for Gemini, Cursor, or Copilot, so all five CLIs use the pinned procedure in `references/review_procedure.md`. Runtime must not search, download, or replace reviewers.
- Read `references/review_procedure.md` and run exactly one candidate-defect scan per round. The first round uses cumulative change scope; correction rounds use only the current `Correction Impact Set`. Candidate findings do not replace this phase's acceptance, risk, ledger, or handoff decisions.
- Treat pinned-procedure `Critical` and `Important` labels and numbers as confidence buckets, not impact severity. Record `Impact: Critical | Important | Minor` separately from `Confidence: 0-100`; do not use `P1` for finding severity or reviewer priority.
- Mark `Impact: Critical` only with a reachable production path, causal or reproducible evidence, and privilege escalation, secret exposure, irreversible data loss, destructive external mutation, or a core workflow failure without safe recovery. High confidence or unbounded theoretical input is insufficient.
- Rounds 1–3 are discovery mode: new evidenced in-scope Critical or Important findings may block, but complete the relevant scope before handoff. From round 4, unresolved lineages, regressions caused by the current correction, and qualifying new Critical findings block; other new findings become follow-up proposals.
- Assign stable `BLK-NNN` IDs to blockers and `FUP-NNN` IDs to follow-ups. Reuse the lineage ID across correction rounds.
- Keep at most 100 canonical `## Todo List` rows. An open blocker uses ``- [ ] `BLK-NNN` — Source: `review` — Work: ... — Closure: ... — Evidence: ...``; an empty list uses only `No actionable work.`. Checkboxes outside that section and follow-up proposals are not correction work.
- Carry the complete prior Finding Registry, including closed and handled lineages; update status and evidence HEAD from current evidence without deleting or renumbering identity.
- This phase proposes follow-ups but does not open, comment on, or close GitHub issues. A follow-up never releases a Critical finding.
- If a CLI later provides a composable native review Skill, follow the `write-cafe-phase` selection matrix and obtain user confirmation before changing this skill; do not run nested CLI subprocesses as native Skills.
- Prioritize behavioral regressions, missing tests, and high-risk findings.
- Follow the shared skill's **Bounded repository inspection** limits; this skill does not repeat them.
- Follow the shared skill's **Repository-owned quality gates** division for evidence, hooks, and CI; this skill does not repeat it.
- Select and assess targeted tests for the change; do not reject Develop for a missing verification receipt and do not rerun repository-wide validation in review.
- The first round establishes acceptance closure and triggered-risk coverage. A correction round reopens only prior blockers, rows affected by the current correction, and new findings that remain blocking under the current mode. Unchanged proven boundaries may be referenced as `closed_reused`, but required cross-component seam coverage remains mandatory.
- Follow the shared skill's **Develop and review disagreement protocol** and **Shared Rules** for developer discussion, arbitration, and blackboard/baton updates.
- Write the next-step baton for this result; the runtime updates the blackboard.
- If changes are required, write the next-step baton to `develop`.
- When complete, write the next workflow step (the default playbook uses `pr`); unresolved non-Critical follow-up proposals are not blockers but must remain in the review output for the PR gate.
- Follow the shared skill's **Develop and review disagreement protocol** and **Shared Rules** for developer discussion, arbitration, and blackboard/baton updates.

## Output
Write review result to: {output_file}

## Handoff
- Write the next-step baton for this result; the runtime updates the blackboard.
