---
name: cafe-spec_plan
description: Discuss requirements and an implementation plan with a native subagent, then submit both for joint confirmation before development.
version: 1.1.1
workflow:
  execution_profile:
    workload: planning
    reasoning: high
    risk_domains: [product-scope, architecture, integration]
    fallback_strength: equivalent_or_stronger
  required_tools: [Agent, Bash]
  human_tasks:
    - id: output-review
      pattern: confirm_output
      prompt: {message_key: human_task.cafe_spec_plan.output_review.prompt}
      prompt_locales:
        zh-TW: {message_key: human_task.cafe_spec_plan.output_review.prompt}
      input_schema: decision
      decisions:
        - id: confirm
          label: {message_key: human_task.cafe_spec_plan.output_review.decisions.confirm.label}
          label_locales:
            zh-TW: {message_key: human_task.cafe_spec_plan.output_review.decisions.confirm.label}
        - id: revise
          label: {message_key: human_task.cafe_spec_plan.output_review.decisions.revise.label}
          label_locales:
            zh-TW: {message_key: human_task.cafe_spec_plan.output_review.decisions.revise.label}
          requires_feedback: true
          correction: true
    - id: clarification-answers
      pattern: answer_questions
      prompt: {message_key: human_task.cafe_spec_plan.clarification_answers.prompt}
      prompt_locales:
        zh-TW: {message_key: human_task.cafe_spec_plan.clarification_answers.prompt}
      input_schema: answers
      questions_from_xml: true
    - id: discussion-recovery
      pattern: revision_feedback
      prompt: {message_key: human_task.cafe_spec_plan.discussion_recovery.prompt}
      prompt_locales:
        zh-TW: {message_key: human_task.cafe_spec_plan.discussion_recovery.prompt}
      input_schema: feedback
    - id: permission-feedback
      pattern: revision_feedback
      prompt: {message_key: human_task.cafe_spec_plan.permission_feedback.prompt}
      prompt_locales:
        zh-TW: {message_key: human_task.cafe_spec_plan.permission_feedback.prompt}
      input_schema: feedback
  prompt_inputs:
    - artifacts: [plan]
      placeholder: prior_plan_file
      required: false
  checklist:
    context_references:
      xml_questions_instruction: xml_questions_instruction.md
    variants:
      - when: {}
        sections:
          - reference: execution_steps.md
    include_role_guidance: true
---

# Discuss Spec and Plan

## Role
Read your agent file: {agent_file}

## Context
- Previous combined requirements and plan, when supplied: {prior_plan_file}
- Use the runtime-provided initial request, development guidance, and current
  user feedback. The complete `plan` artifact contains both the requirements
  authority and the executable implementation worklist; there is no separate
  spec artifact.

## Instructions

### Joint drafting and discussion

1. Read the request and bounded repository evidence. Preserve original input
   verbatim under `## Initial Requirements`; retain any supplied issue title
   and development guidance. Separate requested behavior, acceptance criteria,
   exclusions, and unresolved assumptions from implementation choices. On
   first entry, read the initial input seeded at `{output_file}` before
   replacing it. Before any reactive handoff, durably save that original
   input, issue title, guidance, known answers, and unresolved questions using
   the provisional contract below. Resume from those saved sections and merge
   new answers; never replace the original request with the current answer.
2. Reuse configured project principles and mandate boundaries. If a material
   product, deployment, cost, or maintenance decision lacks user evidence,
   ask only the missing questions using `questions.xml` and route
   `need_clarification` to `user`. A subagent may identify an assumption but
   cannot answer on the user's behalf or authorize scope or external actions.
3. Draft a complete combined document with the sections below. Keep the whole
   draft unconfirmed and non-executable until joint approval. This phase has
   one final planned confirmation; it does not use the separate solution
   alignment checkpoint from `cafe-plan`. Do not load that phase as an overlay.
4. Start one native subagent as a read-only `planning_partner`. Give it the
   original request, current complete draft, current repository evidence, and
   known user decisions. First discuss requirements coverage, acceptance,
   omissions, overreach, and assumptions; then discuss implementation
   feasibility, architectural placement, task ordering, and test coverage.
   Reuse the partner within the session or recreate it from checkpoint evidence
   after an interruption. The parent owns all document edits and decisions.
5. Ask the partner for concrete findings and an explicit conclusion for each
   discussion target, `spec` and `plan`. Persist each response before another
   call. Resolve supported findings, explain disputed findings with evidence,
   and obtain a fresh conclusion rather than dismissing a blocker. Any draft
   edit invalidates both previous conclusions. Review the revised complete
   draft again; a subagent's agreement never substitutes for user confirmation.
6. Limit unresolved discussion to three rounds for one draft decision. If a
   material disagreement remains, present both positions and a recommendation
   through `need_clarification`; do not silently choose the user's requirements.
   If native subagents are unavailable or fail to finish, report the actual
   limitation and pause through `manual_handoff` to `user`. Never bypass the
   discussion or fabricate a conclusion.
7. Before requesting approval, verify both targets have explicit no-blocking
   conclusions for the same current draft and evidence. Show the user the
   complete requirements, scope, approach, tradeoffs, Test List, and Todo List;
   route `confirm_output` to `user`. No source code, tests, commits, or external
   mutations are performed in this phase.
8. On revision, update both affected requirements and implementation work in
   this phase, invalidate discussion receipts, repeat discussion, and request
   joint confirmation again. Continue only from an explicit confirmation for
   this exact output or the runtime's authorized stop-contract decision. If
   draft bytes or repository dependencies changed after that decision, review
   and confirm again rather than advancing with stale approval. Confirmation
   does not rewrite the draft payload or flip a status label; the runtime
   receipt is the acceptance authority and preserves the reviewed payload digest.

### Plan and identity contract

- Read `references/test_invariants_policy.md` before drafting the Test List.
  This is the existing CAFE planning policy, reused without a new domain
  methodology. Distinguish targeted development checks from repository-owned
  hook, CI, coverage, and release gates.
- Put executable work only in one `## Todo List`, at most 100 stable rows:
  ``- [ ] `PLAN-NNN` — Source: `plan` — Work: ... — Closure: ... — Evidence: ...``.
  Each field is non-empty. For a genuinely empty implementation scope, write
  exactly `No actionable work.`; never use it to disguise unfinished planning.
- When `prior_plan_file` exists, read it and its sibling `artifact.json` before
  revising Todo identities. Retain IDs for continuing work, never reuse an ID
  for unrelated work, and preserve unchanged IDs across reordering. For a
  changed retained Work, add `## Todo Identity Continuity` after the Todo List
  with ``- `PLAN-NNN` — Previous work fingerprint: `<sha256>` `` copied exactly
  from the prior artifact's `todo_work_identities` metadata. Do not invent a
  fingerprint or alter the accepted prior artifact. If the prior output is
  provisional, follow its `todo_identity_baseline` reference to the exact
  detailed plan and metadata; a null baseline means no prior Todo authority.
  Carry existing work identities through repeated clarification rounds.

### Provisional clarification contract

- Before a detailed draft can be written, use
  `references/clarification_draft.md` to save the known input and questions to
  `{output_file}`. Its first non-blank line is exactly
  `<!-- plan-stage: solution-alignment -->`. This is the runtime's existing
  provisional data format, not an additional solution-direction approval.
- Preserve original input and supplied guidance verbatim, record substantive
  answers and open questions, and mark the document unconfirmed and
  non-executable. Do not include a Test List, `## Todo List`, Todo Identity
  Continuity, or implementation tasks in a provisional output. Do not invent
  `No actionable work.` for an unfinished plan.
- A provisional output may pause for clarification, permission, or discussion
  recovery only. Never send it to joint `confirm_output` or downstream
  execution. The runtime retains the last detailed plan's identity baseline
  while the provisional output owns no Todo authority.
- Once enough information exists, replace the provisional marker with
  `<!-- plan-stage: detailed-plan -->`, complete the combined draft and its
  canonical Todo List, and discuss that exact payload before joint approval.
  When a detailed plan already exists, retain its valid Todo authority through
  minor clarification; use the provisional form only if scope or direction
  must be reopened, retaining the runtime's referenced baseline.

### Checkpoint and resume

- Keep the sole resume ledger in `## Discussion Checkpoint` at the end of
  `{output_file}`. Downstream readers consume the complete document but treat
  this section as discussion evidence, never as extra implementation tasks.
  Keep a valid complete draft before any subagent call. Before an early pause,
  save the original request and known guidance using the provisional contract
  even when no previous output exists; never leave a markerless partial plan.
- Store schema version `1`, the stable target set `[spec, plan]`, round counts,
  and each target's pending/done status, dependency fingerprint, partner
  conclusion, findings, and sanitized response evidence. Include a SHA-256
  run-context fingerprint of original input and substantive user decisions
  and revision feedback (exclude the pure confirmation response itself), plus
  a repository fingerprint covering HEAD, current diff, and contents of all
  relevant dirty/untracked files. Record the relevant file set explicitly.
- Define the `draft-v1` payload as the exact UTF-8 bytes before the unique
  `## Discussion Checkpoint` heading. Each target depends on the SHA-256 of
  that complete payload, the run context, and repository fingerprint. Do not
  hash ledger fields into their own digest. Checkpoint atomically after each
  partner response; the maximum lost work is one unfinished partner call.
- On retry, recompute these fingerprints before further discussion. Trust a
  done row only when its dependency fingerprints still match and its explicit
  response evidence exists. Changed input, any draft edit, or ambiguous impact
  reopens both targets and global finalization. Missing legacy checkpoints
  start pending; deterministic draft content may be retained, but discussion
  and user approval are never inferred from prose or iteration numbers.
- Before the approval handoff, perform a global sweep of the current draft,
  repository evidence, and both conclusions. Record `finalized` with algorithm
  `sha256`, projection `draft-v1`, and that exact payload digest. Revalidate the
  receipt on resume. Retain the checkpoint through runtime completion; cleanup
  belongs to a later retention policy. User approval remains runtime-owned
  HumanTask/stop-contract evidence and is never manufactured in this ledger.

## Output
Write combined requirements and implementation plan to: {output_file}

Use these headings; fill every section before joint approval:

- `## Initial Requirements` — original request and supplied issue title.
- `## Development Guide` — supplied guidance, or explicit absence.
- `## Requirements Specification` — user-visible behavior, acceptance criteria,
  exclusions, and resolved assumptions. Keep technical decisions below.
- `## Implementation Approach` — recommended direction, will do, will not do,
  and material tradeoffs, or explicit `None`. Include the fixed statement
  `Execution requires runtime-recorded joint approval.`; confirmation applies
  to the entire document and does not change that statement.
- `## Negative space` — dependencies and abstractions declined, with reasons.
- `## Layering map` — concrete file/module boundaries.
- `## Dependency ADR` — dependency decisions and requirement served, or explicit
  `No new dependencies expected.`; justify risk for any proposed recent major.
- `## Test List` — `### Unit tests (N)` and `### Integration tests (M)` with
  labeled invariants and user journeys; explain any zero count.
- `## Todo List` — the single canonical executable worklist.
- `## Discussion Checkpoint` — the retained structured ledger and digest receipt.

Do not add a separate spec file, a hidden progress sidecar, or packet-specific
IDs. The complete combined Markdown remains the semantic authority.

## Handoff
- Write the next-step baton for this result; the runtime updates the blackboard.
