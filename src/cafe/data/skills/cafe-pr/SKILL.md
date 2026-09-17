---
name: cafe-pr
description: "Prepare the local pull request title and description for publication"
version: 1.4.1
workflow:
  execution_profile:
    workload: publication
    reasoning: routine
    risk_domains: [external-side-effects]
    fallback_strength: equivalent
  human_tasks:
    - id: local-review
      pattern: confirm_output
      prompt: Review the prepared local changes and the Follow-up Proposals section in the PR description. Your decision applies to every open FUP; per-proposal mixed disposition is not supported. Fix all proposals now, record that all should become separate issues, or approve and continue without issues.
      input_schema: decision
      decisions:
        - id: fix_now
          label: Fix all proposed items now
          requires_feedback: true
          correction: true
        - id: create_follow_up
          label: Record issues for all proposals
        - id: continue_without_issue
          label: Approve / continue without issues
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
    - artifacts: [qa_feedback, review_feedback]
      placeholder: feedback_file
      required: false
    - artifacts: [review_feedback]
      placeholder: review_feedback_file
      required: false
    - artifacts: [workflow_feedback]
      placeholder: workflow_feedback_file
      required: false
  prompt_references:
    spec_context: pr_spec_context.md
    plan_context: pr_plan_context.md
  checklist:
    context_references:
      spec_read_instruction: spec_read_instruction.md
      plan_read_instruction: plan_read_instruction.md
      review_feedback_instruction: review_feedback_instruction.md
    variants:
      - when: {iteration: 1}
        sections:
          - reference: execution_steps_iteration_1.md
          - optional_checklist: basic_principles.md
      - when: {min_iteration: 2}
        sections:
          - reference: execution_steps_iteration_n.md
          - optional_checklist: basic_principles.md
    include_role_guidance: true
---

# PR

## Role
Read your agent file: {agent_file}

## Context
{spec_context}{plan_context}

## Commits
{commits}

## Verified workspace
Use the declared workspace input when it is supplied by the workflow runtime.

## Available scripts

- **`scripts/sync_pr.sh`** — Push branch, create/update GitHub PR, and (when enabled) post completed todo list comment

```bash
bash scripts/sync_pr.sh --help
```

In workflow mode, do not run this script directly from the agent. The CAFE
host-side `GitHubPRCreator` publish hook runs it after the PR artifact is ready,
so GitHub/network access happens outside the agent sandbox.

When the generic runtime includes a handoff block for the PR step, it repeats
agent-local-first completion and the confirmed workflow publication mode; treat
that text as authoritative alongside this skill. `pr.auto_create: false` means
the workflow is `local-only`, while `true` means the host must publish before
the review task can expose a verified PR URL.

## Instructions

- When `workspace_file` is supplied, use it as the authoritative Git changed-file identity for the prepared PR content.

### Corrective feedback curation mode
When `workflow_feedback_file` contains feedback for this cycle, or `Current user input for this iteration` contains PR review comments, this is PR iteration 2:

 - When runtime provides `workflow_feedback_batch_file`, it is the only immutable source context for this cycle. Select Todo items only from that batch; later items remain for a later cycle. Otherwise, `workflow_feedback_file` and review comments are PR-agent context, not a Develop worklist. Process only unresolved corrective input declared for this step; do not import resolved, stale, duplicate, informational, ordinary PR-body, `## Test Plan`, or open follow-up proposal text.
 - Normalize each applicable source from the current corrective cycle into the output's one `## Todo List` of at most 100 rows. Use the declared Todo source and ID prefix, preserve one-to-one source identity, and never merge distinct sources because their text matches. Use only `No actionable work.` when there is no applicable source.
 - Todo rows must use ``- [ ] `<id>` — Source: `<source>` — Work: ... — Closure: ... — Evidence: ...``. Write only the normalized list; do not include raw PR comments or HumanTask feedback.
 - After curation, write the declared `manual_handoff` using injected `{step_transitions}`. Do not hardcode step names, skip the curator, or select an undeclared route.

### PR content mode
Otherwise (there are no PR review comments):

1. Read the requirements, implementation plan, and current branch commits supplied by the workflow.
2. Edit `{output_file}` with a PR title and description:
   - Put a concise title, no longer than 80 characters, on the first `#` line.
   - Keep the `Summary`, `Changes`, `Test Plan`, and `Follow-up Proposals` structure.
   - Copy each `status: open` `FUP-NNN` ID, impact, confidence, evidence summary, and draft issue title/body from the declared `review_feedback_file`; do not rewrite IDs or invent proposals.
   - Write `None` when there are no open proposals. Otherwise state that one PR HumanTask choice applies to all open `FUP-NNN` items; `create_follow_up` records the request and does not create a GitHub issue automatically.
3. Do not call a GitHub connector or API, `gh pr create`, or `scripts/sync_pr.sh` directly.
4. Do not query or wait for a remote branch or PR; the host-side hook publishes after the agent returns.
5. After the local PR artifact and checklist are complete, choose the next baton from injected `{step_transitions}`. Route `confirm_output` to `user`; complete directly only when `workflow_complete→done` is declared. Do not handle a follow-up proposal on the user's behalf.
6. When `pr.auto_create: true`, the host-side hook runs `scripts/sync_pr.sh --output {output_file}` before human review or completion, adding `--base` from `issue.yaml`. Only a successful result passing the output contract may produce `pr_synced` evidence and a verified PR URL.
7. When `pr.auto_create: false`, the workflow is `local-only`: the hook does not publish or reuse an old URL, and the review task states `Publication mode: local-only. No PR URL exists.`
8. When `{step_transitions}` declares `confirm_output`, only the bound HumanTask approval may complete the workflow; the PR agent must not rewrite it as `done` or `workflow_complete`.

### Publication authority
- PR content and publication follow this phase and the `cafe.pr.publish` capability contract; kickoff questions, options, and prepare parameters come from the capability manifest's `setup_questions`.
- Creating or updating a PR, review approval, and workflow completion do not authorize merge or issue closure; this phase does not perform those actions.
- Merge is separately authorized integration work; do not infer permission from a request to finish the remaining work.

### Gotchas
- Scripts write progress and errors to stderr and structured JSON to stdout.
- An existing PR is updated idempotently rather than recreated.
- Publication failure, permission denial, or a successful receipt without a URL must not create a false `local-review` handoff; approval resume and direct success use the same validated `pr_synced` evidence contract.
- Host-side hooks handle external network access, GitHub credentials, and push/create/update operations.
- A missing remote branch or PR is normal before the hook runs and does not mean the PR phase is incomplete.
- Do not restate PR content in the response; use the blackboard and next-step baton for handoff.

## Output
Write PR content to: {output_file}

## Handoff
- Write the next-step baton for this result; the runtime updates the blackboard.
