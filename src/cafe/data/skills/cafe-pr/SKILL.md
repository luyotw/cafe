---
name: cafe-pr
description: Prepare the local pull request title and description for publication
version: 1.11.0
workflow:
  notification:
    step_label:
      message_key: notification.step_labels.pr
    task_labels:
      local-review:
        message_key: notification.action_labels.local_review
  execution_profile:
    workload: publication
    reasoning: routine
    risk_domains:
    - external-side-effects
    fallback_strength: equivalent
  human_tasks:
  - id: pr-review
    pattern: confirm_output
    prompt: {message_key: human_task.cafe_pr.pr_review}
    prompt_locales:
      zh-TW: {message_key: human_task.cafe_pr.pr_review}
    input_schema: decision
    decisions:
    - id: fix_now
      label: {message_key: human_task.cafe_pr.pr_fix}
      label_locales:
        zh-TW: {message_key: human_task.cafe_pr.pr_fix}
      requires_feedback: true
      correction: true
    - id: confirm
      label: {message_key: human_task.cafe_pr.pr_confirm}
      label_locales:
        zh-TW: {message_key: human_task.cafe_pr.pr_confirm}
  - id: pr-details
    pattern: revision_feedback
    prompt: {message_key: human_task.cafe_pr.pr_details}
    prompt_locales:
      zh-TW: {message_key: human_task.cafe_pr.pr_details}
    input_schema: feedback
  - id: local-review
    pattern: confirm_output
    prompt:
      message_key: human_task.cafe_pr.local_review.prompt
    prompt_locales:
      zh-TW:
        message_key: human_task.cafe_pr.local_review.prompt
    input_schema: decision
    decisions:
    - id: fix_now
      label:
        message_key: human_task.cafe_pr.local_review.decisions.fix_now.label
      label_locales:
        zh-TW:
          message_key: human_task.cafe_pr.local_review.decisions.fix_now.label
      requires_feedback: true
      correction: true
    - id: create_follow_up
      label:
        message_key: human_task.cafe_pr.local_review.decisions.create_follow_up.label
      label_locales:
        zh-TW:
          message_key: human_task.cafe_pr.local_review.decisions.create_follow_up.label
    - id: continue_without_issue
      label:
        message_key: human_task.cafe_pr.local_review.decisions.continue_without_issue.label
      label_locales:
        zh-TW:
          message_key: human_task.cafe_pr.local_review.decisions.continue_without_issue.label
  - id: delivery-review
    pattern: confirm_output
    prompt:
      message_key: human_task.cafe_pr.delivery_review.prompt
    prompt_locales:
      zh-TW:
        message_key: human_task.cafe_pr.delivery_review.prompt
    input_schema: decision
    decisions:
    - id: fix_now
      label:
        message_key: human_task.cafe_pr.delivery_review.fix_now
      label_locales:
        zh-TW:
          message_key: human_task.cafe_pr.delivery_review.fix_now
      requires_feedback: true
      correction: true
    - id: integrate_selected
      label:
        message_key: human_task.cafe_pr.delivery_review.integrate_selected
      label_locales:
        zh-TW:
          message_key: human_task.cafe_pr.delivery_review.integrate_selected
      requires_feedback: true
    - id: integrate_only
      label:
        message_key: human_task.cafe_pr.delivery_review.integrate_only
      label_locales:
        zh-TW:
          message_key: human_task.cafe_pr.delivery_review.integrate_only
    - id: review_only
      label:
        message_key: human_task.cafe_pr.delivery_review.review_only
      label_locales:
        zh-TW:
          message_key: human_task.cafe_pr.delivery_review.review_only
  - id: delivery-details
    pattern: revision_feedback
    prompt:
      message_key: human_task.cafe_pr.delivery_details.prompt
    prompt_locales:
      zh-TW:
        message_key: human_task.cafe_pr.delivery_details.prompt
    input_schema: feedback
  prompt_inputs:
  - artifacts:
    - spec
    placeholder: spec_file
    required: false
    load_policy:
    - mode: packet
      contract_kind: spec
  - artifacts:
    - spec
    placeholder: spec_file_path
    required: false
    load_policy:
    - mode: packet
      contract_kind: spec
  - artifacts:
    - plan
    placeholder: plan_file
    required: false
    load_policy:
    - mode: packet
      contract_kind: plan
  - artifacts:
    - plan
    placeholder: plan_file_path
    required: false
    load_policy:
    - mode: packet
      contract_kind: plan
  - artifacts:
    - code
    placeholder: develop_file
    required: false
  - artifacts:
    - workspace
    placeholder: workspace_file
    required: false
  - artifacts:
    - qa_feedback
    - review_feedback
    placeholder: feedback_file
    required: false
  - artifacts:
    - review_feedback
    placeholder: review_feedback_file
    required: false
  - artifacts:
    - workflow_feedback
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
    - when:
        iteration: 1
      sections:
      - reference: execution_steps_iteration_1.md
      - optional_checklist: basic_principles.md
    - when:
        min_iteration: 2
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

 - When runtime provides `workflow_feedback_batch_file`, it is the only immutable source context for this cycle. Select Todo items only from that batch; later items remain for a later cycle. Use the paired ID and Source from runtime's `Canonical Todo fields for this batch` block exactly as shown for each selected batch entry; do not derive or substitute a generic PR-comment prefix or source. Otherwise, `workflow_feedback_file` and review comments are PR-agent context, not a Develop worklist. Process only unresolved corrective input declared for this step; do not import resolved, stale, duplicate, informational, ordinary PR-body, `## Test Plan`, or open follow-up proposal text.
 - Decide which sources in the current corrective batch need implementation. Normalize each applicable source into the output's one `## Todo List` of at most 100 rows. Preserve one-to-one source identity, and never merge distinct sources because their text matches.
 - For applicable work, Todo rows must use ``- [ ] `<id>` — Source: `<source>` — Work: ... — Closure: ... — Evidence: ...``. Write only the normalized list; do not include raw PR comments or HumanTask feedback.
 - When this batch has applicable corrective work, write the declared `manual_handoff`. Prefer an injected discretionary route marked `carries_feedback`; when none exists, use the injected `defaults.manual_handoff` destination only if it is also listed in the injected `goto` entries.
 - This declared curation handoff delivers the current normalized `{output_file}` Todo List. Preserve all runtime-assigned IDs and Sources, including a batch with multiple source kinds. Runtime validates the receiving phase's declared artifact input and canonical worklist before transition.
 - Do not change the incoming feedback target, hardcode step names, select an undeclared route, or hand raw feedback to an execution phase. If neither route is available, report the missing curation route through the declared output-review handoff instead of inventing a destination.
 - When this batch has no applicable corrective work, prepare the complete PR title and description using the PR content steps below. Include exactly one `## Todo List` containing only `No actionable work.` so the runtime can settle this batch as excluded. Choose the declared `confirm_output` route to `user`, or the declared `workflow_complete` default to `done` when no review gate exists. Do not send an empty worklist to a correction consumer or decide the user's follow-up proposals.

### PR content mode
When there is no corrective feedback for this cycle, or this batch has no applicable corrective work:

1. Read the requirements, implementation plan, and current branch commits supplied by the workflow.
2. Edit `{output_file}` with a PR title and description:
   - Put a concise title, no longer than 80 characters, on the first `#` line.
   - Keep the `Summary`, `Changes`, `Test Plan`, and `Follow-up Proposals` structure.
   - Copy each `status: open` `FUP-NNN` ID, impact, confidence, evidence summary, and draft issue title/body from the declared `review_feedback_file`; do not rewrite IDs or invent proposals.
   - Write `None` when there are no open proposals. For legacy `local-review`, one PR HumanTask choice applies to all open proposals; it does not create a GitHub issue automatically. When the declared task is `delivery-review`, display every original draft and request explicit proposal IDs for `integrate_selected`; `integrate_only` selects none. `review_only` authorizes no delivery effect.
3. Do not call a GitHub connector or API, `gh pr create`, or `scripts/sync_pr.sh` directly.
4. Do not query or wait for a remote branch or PR; the host-side hook publishes after the agent returns.
5. After the local PR artifact and checklist are complete, choose the next baton from the injected route catalog. Route `confirm_output` to `user`; complete directly only when the catalog declares a `workflow_complete` default to `done`. Do not handle a follow-up proposal on the user's behalf.
6. When `pr.auto_create: true`, the host-side hook runs `scripts/sync_pr.sh --output {output_file}` before human review or completion, adding `--base` from `issue.yaml`. Only a successful result passing the output contract may produce `pr_synced` evidence and a verified PR URL.
7. When `pr.auto_create: false`, the workflow is `local-only`: the hook does not publish or reuse an old URL, and the review task states `Publication mode: local-only. No PR URL exists.`
8. When the injected route catalog declares a `confirm_output` default, only the bound HumanTask approval may complete the workflow; the PR agent must not rewrite it as `done` or `workflow_complete`.

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

### Publication and delivery boundary

Complete the PR content, publication and declared content review without requiring an integration strategy, local destination checkout or verification tool selection. Use the `pr-review` task when bound: `confirm` approves the PR content and continues; it never authorizes merge or follow-up issue creation. Include original open Follow-up Proposals for the delivery phase to assess and select later. Known missing implementation checks belong in the current Todo List and correction route, but missing delivery configuration must not block PR completion.

The selected delivery phase owns strategy recommendation, target identity, verification scope, and the exact action permission bundle. It prepares and confirms that bundle before execution. Do not prepare `delivery_request.json` for a new delivery-owned binding or ask the user to choose a merge mode here. Legacy project catalogs with an explicit PR-owned `delivery` binding retain their existing task contract and cannot turn content confirmation into action approval.

## Output
Write PR content to: {output_file}

## Handoff
- Write the next-step baton for this result; the runtime updates the blackboard.
