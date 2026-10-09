---
name: cafe-deliver_development
description: Verify development delivery with approved project tools, help close missing
  verification work, and request acceptance of complete results.
version: 1.1.0
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
  - id: delivery-outcome
    pattern: confirm_output
    prompt:
      message_key: human_task.delivery.delivery_outcome
    prompt_locales:
      zh-TW:
        message_key: human_task.delivery.delivery_outcome
    input_schema: decision
    decisions:
    - id: confirm_cleanup
      label: {message_key: human_task.delivery.confirm_cleanup}
      label_locales:
        zh-TW: {message_key: human_task.delivery.confirm_cleanup}
    - id: confirm_archive
      label: {message_key: human_task.delivery.confirm_archive}
      label_locales:
        zh-TW: {message_key: human_task.delivery.confirm_archive}
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

The declared host hook owns integration and external operations. Read the current action and result paths supplied by the host. Report the actual integration commit, selected proposal IDs and issue URLs, remaining actions, and the verification tool's evidence in {output_file}. Perform no direct Git/GitHub mutation.

Use the project's existing CI and verification tools first. CAFE does not implement every CI provider. For GitHub Actions the optional bundled observer is available; for other platforms select an existing, reviewable tool suited to the agreed delivery scope. Read `references/verification_tools.md` when selecting, explaining, or helping implement a tool. Never require every repository to add a script or silently replace a missing tool with the GitHub observer.

When a necessary tool or check is missing, explain the gap and help implement it: include a concrete script draft and fixture-based test cases in the delivery output when useful. Run local tests only with the granted tools. Normalize the remaining implementation, tests and integration into the existing Todo List and use the injected correction route. Repo changes go through the existing development, review and PR process before the host executes the revised tool. Do not require a separate issue for a small correction; propose a follow-up only when independent work is appropriate. A proposal is not permission to create an external issue. Missing credentials or an unresolved acceptance requirement need the declared permission/clarification route.

The host executes only the exact approved tool bytes and options under the reviewed capability boundary. A normal check returns pending, succeeded, failed or unknown for the actual integration commit. Pending checks and retryable transport errors are normal machine waiting: the background worker saves the latest observation, releases the workspace lease, and resumes the same iteration without an agent call or repeated integration. Fifteen minutes is not an acceptance or recovery deadline. A stopped worker resumes from saved evidence on the next `cafe make`; operating-system startup does not automatically launch one.

For a failed required check, normalize corrections into the existing Todo List and use the injected correction route. Unknown or missing evidence never proves success. Tools and verification scope changed after approval need fresh PR action review; preserve completed integration effects and never edit old approval bytes. Legacy snapshots missing verification scope follow that review route.

Only when `delivery_complete` is true, including every agreed verification or an explicitly approved no-verification reason, request the declared user `confirm_output` handoff. Display the terminal plan with the results; the same reply accepts the outcome and chooses cleanup, archive-only or leaving external state unchanged. Manager executes that exact choice after workflow completion; this phase performs no cleanup.

## Output

Write the delivery summary to: {output_file}

## Handoff

Write next-step baton for this result; the runtime updates the blackboard.
