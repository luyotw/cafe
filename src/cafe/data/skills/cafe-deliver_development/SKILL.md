---
name: cafe-deliver_development
description: Verify development delivery with approved project tools, help close missing
  verification work, and request acceptance of complete results.
version: 1.3.0
workflow:
  execution_profile:
    workload: publication
    reasoning: routine
    risk_domains:
    - external-side-effects
    fallback_strength: equivalent
  prompt_inputs:
    - artifacts: [workflow_feedback]
      placeholder: workflow_feedback_file
      required: false
  human_tasks:
  - id: delivery-review
    pattern: confirm_output
    prompt:
      message_key: human_task.delivery.delivery_review.prompt
    prompt_locales:
      zh-TW:
        message_key: human_task.delivery.delivery_review.prompt
    input_schema: decision
    decisions:
    - id: fix_now
      label:
        message_key: human_task.delivery.delivery_review.fix_now
      label_locales:
        zh-TW:
          message_key: human_task.delivery.delivery_review.fix_now
      requires_feedback: true
      correction: true
    - id: integrate_selected
      label:
        message_key: human_task.delivery.delivery_review.integrate_selected
      label_locales:
        zh-TW:
          message_key: human_task.delivery.delivery_review.integrate_selected
      requires_feedback: true
    - id: integrate_only
      label:
        message_key: human_task.delivery.delivery_review.integrate_only
      label_locales:
        zh-TW:
          message_key: human_task.delivery.delivery_review.integrate_only
    - id: review_only
      label:
        message_key: human_task.delivery.delivery_review.review_only
      label_locales:
        zh-TW:
          message_key: human_task.delivery.delivery_review.review_only
  - id: delivery-outcome
    pattern: confirm_output
    prompt:
      message_key: human_task.delivery.delivery_outcome
    prompt_locales:
      zh-TW:
        message_key: human_task.delivery.delivery_outcome
    input_schema: decision
    decisions:
    - id: confirm
      label: {message_key: human_task.delivery.confirm}
      label_locales:
        zh-TW: {message_key: human_task.delivery.confirm}
    - id: revise
      requires_feedback: true
      correction: true
      label: {message_key: human_task.delivery.revise}
      label_locales:
        zh-TW: {message_key: human_task.delivery.revise}
  - id: delivery-recovery
    pattern: revision_feedback
    prompt:
      message_key: human_task.delivery.delivery_recovery
    prompt_locales:
      zh-TW:
        message_key: human_task.delivery.delivery_recovery
    input_schema: feedback
  - id: delivery-permission
    pattern: revision_feedback
    prompt:
      message_key: human_task.delivery.delivery_permission
    prompt_locales:
      zh-TW:
        message_key: human_task.delivery.delivery_permission
    input_schema: feedback
  checklist:
    variants:
    - when: {}
      sections:
      - reference: delivery.md
    include_role_guidance: true
---

# Development delivery

## Role

Read your agent file: {agent_file}

## Available scripts

- `scripts/verify_github_actions.py` — Read existing target-branch Actions results for the approved commit; this optional GitHub tool does not trigger CI or deployment.

## Instructions

Prepare the integration plan here, after PR content confirmation. Read the declared PR publication input and existing repository policy. Recommend a concrete GitHub merge strategy consistent with that policy and platform settings; when no policy exists, propose a suitable strategy in the complete bundle for approval. Missing strategy configuration is not a separate questionnaire. Explain material conflicts and request clarification only for an unresolved target, unavailable local checkout or a decision that cannot be safely proposed.

Before action permission, write `delivery_request.json` beside {output_file}, with only `mode` (`github` or `local`), `strategy`, `target_branch`, `destination` (absolute clean checkout for local), optional `issue_repository` for selected follow-ups, and `verification`. Derive verification from confirmed scope using `references/verification_tools.md`; prefer suitable existing tools. The host binds and displays the actual source, target, published PR, original Review proposals, tool and capability boundaries. Request `need_permission` for this exact bundle; `integrate_only` selects no follow-ups, `integrate_selected` selects only explicitly supplied `FUP-NNN` IDs, and `review_only`/`fix_now` authorize no action.

Stage comes from the current correlated action task and immutable proposal, never the iteration number. No task means prepare the plan; a pending task waits for its exact answer; only a completed matching `integrate_*` decision permits execution. The existing PR input remains immutable approval evidence while the delivery output changes into a result. Missing or stale authority cannot become permission through a content confirmation, Manager answer or a revised draft. Preserve successful effects when a fresh action review is needed.

The declared host hook owns integration and external operations. Read the current action and result paths supplied by the host. Report the actual integration commit, selected proposal IDs and issue URLs, remaining actions, and the verification tool's evidence in {output_file}. Perform no direct Git/GitHub mutation.

Use the project's existing CI and verification tools first. CAFE does not implement every CI provider. For GitHub Actions the optional bundled observer is available; for other platforms select an existing, reviewable tool suited to the agreed delivery scope. Read `references/verification_tools.md` when selecting, explaining, or helping implement a tool. Never require every repository to add a script or silently replace a missing tool with the GitHub observer.

When a necessary tool or check is missing, explain the gap and help implement it: include a concrete script draft and fixture-based test cases in the delivery output when useful. Run local tests only with the granted tools. Normalize the remaining implementation, tests and integration into the existing Todo List and use the injected correction route. Repo changes go through the existing development, review and PR process before the host executes the revised tool. Do not require a separate issue for a small correction; propose a follow-up only when independent work is appropriate. A proposal is not permission to create an external issue. Missing credentials or an unresolved acceptance requirement need the declared permission/clarification route.

The host executes only the exact approved tool bytes and options under the reviewed capability boundary. A normal check returns pending, succeeded, failed or unknown for the actual integration commit. Pending checks and retryable transport errors are normal machine waiting: the background worker saves the latest observation, releases the workspace lease, and resumes the same iteration without an agent call or repeated integration. Fifteen minutes is not an acceptance or recovery deadline. A stopped worker resumes from saved evidence on the next `cafe make`; operating-system startup does not automatically launch one.

For a failed required check, normalize corrections into the existing Todo List and use the injected correction route. Unknown or missing evidence never proves success. Tools and verification scope changed after approval need fresh delivery action review; preserve completed integration effects and never edit old approval bytes. Legacy snapshots missing verification scope follow that review route.

Only when `delivery_complete` is true, including every agreed verification or an explicitly approved no-verification reason, request the declared user `confirm_output` handoff. Display only the completed delivery results and verification evidence. The reply accepts or revises those results. Manager owns the separately confirmed closeout choice and plan, and executes it after workflow completion. This phase does not display or select cleanup, archive, or resource retention.

## Output

Write the delivery summary to: {output_file}

## Handoff

Write next-step baton for this result; the runtime updates the blackboard.
