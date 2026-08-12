# Kickoff And Preparation

Read this reference before presenting a kickoff, preparing an issue, resuming an
issue whose first `cafe make` has not run, or answering a question about the
workflow conversation language. Also read `strategic_context.md`.

## Conversation locale checklist

- [ ] Resolve the effective locale in this priority order:
  1. a locale the user directly requested for this thread;
  2. a locale reliably inferred from the user's own natural-language messages
     in the current thread;
  3. the active playbook's `playbook.conversation_locale`.
- [ ] Infer a preference when the user's current request clearly uses one
  language, or when multiple user messages consistently use it. Do not infer
  from quoted text, pasted artifacts, code, commands, proper nouns, or an
  isolated token. If the evidence is mixed or ambiguous, use the playbook
  locale.
- [ ] Resolve the active playbook from the user's request or `.cafe/config.yaml`.
- [ ] Run `cafe playbook confirmation-gates <playbook-id>` and read both the
  `Conversation locale:` line and confirmation-gate candidates.
- [ ] Treat a configured explicit BCP 47 value as the fallback, not an override
  of a direct or reliably inferred user preference. For `auto`, infer from the
  user's messages using the same rules above.
- [ ] Include the effective value and source in the kickoff, for example:
  `conversation_locale: zh-TW (inferred user preference from current thread)`
  or `conversation_locale: en-US (from playbook: default)`. Locale is a
  required kickoff field, not a confirmation gate.
- [ ] Apply it to kickoff, clarification, permission, alignment, progress,
  error, and completion messages. Preserve commands, paths, playbook and step
  names, intents, artifact keys, payload fields, and quoted source text.
- [ ] Honor a direct thread language override over every other source. Merely
  writing in another language is an inference signal, not a direct override;
  asking why a language was used is not an override.
- [ ] If asked about the language choice, report the configured value,
  effective value, inference evidence when applicable, and source. Never claim
  this skill lacks a locale rule.

Do not copy the locale into `issue.yaml`. Re-resolve it when starting or
resuming and whenever the playbook changes.

## Repository content locale checklist

- [ ] Treat conversation language and repository content language as separate
  decisions. Use one repository content locale for both documentation and code
  comments by default.
- [ ] Before `cafe init` or any other repository mutation, explicitly ask the
  user to confirm `repository_content_locale`.
- [ ] Recommend the effective conversation locale when the user has not supplied
  a preference, but do not treat inference or a playbook locale as confirmation
  of the repository content language.
- [ ] Preserve programming-language identifiers, commands, paths, protocol
  fields, and established technical terms regardless of the selected locale.
- [ ] If the user explicitly needs documentation and comments to differ, record
  that as a scoped exception instead of making two languages a routine kickoff
  decision.
- [ ] Include the proposed value in the kickoff formatter output. Acceptance of
  the complete kickoff contract explicitly confirms it.
- [ ] Persist the confirmed value in `.cafe/strategic_context.yaml` as repository-
  wide conventions, not in issue-owned workflow state:

  ```yaml
  repository_language:
    content_locale: zh-TW
    confirmed_by: user
    confirmed_at: 2026-08-12
  ```

- [ ] On resume, reuse this confirmed repository-wide value. Reconfirm before
  mutation when it is absent, unconfirmed, or the user requests a change.

## Kickoff contract: first blocking gate

Before `cafe prepare`, any repository mutation, or the first `cafe make`, obtain
explicit user confirmation of:

- `playbook_id`;
- `conversation_locale` with source;
- `repository_content_locale`;
- every planned confirmation gate, partitioned into `user_required` and
  `driver_confirmable`;
- `reactive_user_handoffs`;
- mandate preset, axes, levels, and out-of-mandate list;
- worktree choice and path when using a worktree.

Do not reuse another issue's contract or a repository proposal silently. For an
existing issue, honor its confirmed contract and reconfirm only when it is
missing, invalid, or stale.

### Derive confirmation gates

1. Run:
   ```bash
   cafe playbook confirmation-gates <playbook-id>
   ```
2. Treat exactly the reported steps as candidates. They come from
   `steps.<step>."on".confirm_output`.
3. Present each candidate by step and purpose. Recommend that all candidates
   stop for the user, then ask the user to assign every candidate to exactly
   one of:
   - `user_required`: stop for the real user;
   - `driver_confirmable`: the driver may verify and continue.
4. Require the two lists to be disjoint and their union to equal the candidates.
   Reject unknown steps, missing candidates, overlaps, role names, and steps
   that do not declare `on.confirm_output`.
5. If no candidates exist, explicitly confirm that there are no scheduled
   confirmation stops.

If the playbook, effective conversation locale, repository content locale, or
the candidate set changes, reconfirm the contract before the next `cafe make`.

`need_clarification` and `need_permission` are reactive interruptions, not
scheduled candidates. `manual_handoff` is routing, not a planned confirmation
gate. Alignment is a proactive driver decision governed by mandate. Record the
reactive policy in the kickoff:

- `need_clarification`: user required unless the exact answer already exists in
  the current thread;
- `need_permission`: user required unless the exact permission already exists
  in the current thread;
- `alignment_checkpoint`: driver-resolvable only when the proposal is clearly
  within confirmed strategy and mandate.

Any other runtime `to_owner=user` baton or `Workflow is waiting for user input`
output is a hard stop.

### Render the proposal

Use the bundled formatter instead of a prose-only summary:

```bash
python3 <skill-dir>/scripts/format_kickoff_contract.py <playbook-id> \
  --issue-name <issue-name> \
  --effective-locale <locale> \
  --locale-source "<playbook or direct-user-override source>" \
  --repository-content-locale <locale> \
  --user-required <steps...> \
  --driver-confirmable <steps...> \
  --worktree .cafe/worktrees/<issue-name>
```

Pass an option with no step values for an explicit empty list. The formatter
validates the partition and includes every phase, role, skill, scheduled gate,
owner, stop behavior, reactive policy, mandate boundary, conversation locale
source, repository content locale, and worktree choice. It re-executes with
the Python interpreter that owns `cafe` when the shell interpreter lacks CAFE
dependencies.

If the user already chose values in the current request, render and restate them
for confirmation rather than asking again.

## Preparation checklist

- [ ] Inventory strategic documents and authority using
  `strategic_context.md`; co-create any required missing document before
  `cafe make`.
- [ ] Check `git status --short --branch`.
- [ ] Verify `repository_content_locale` was explicitly confirmed and persist
  it in `.cafe/strategic_context.yaml`.
- [ ] If needed, initialize with `cafe init --preset <preset>`.
- [ ] Recommend a worktree at `.cafe/worktrees/<issue-name>` by default. If the
  user accepts the recommended kickoff unchanged, worktree creation is approved.
- [ ] Prepare non-interactively:

  ```bash
  cafe prepare <issue-name> --no-interactive --input-method=manual \
    --rigor=medium --spec-template=auto --plan-template=default \
    --worktree .cafe/worktrees/<issue-name>
  ```

  For a GitHub issue:

  ```bash
  cafe prepare <issue-name> --no-interactive --input-method=github \
    --issue-id=<number> --rigor=medium --spec-template=auto \
    --plan-template=default --worktree .cafe/worktrees/<issue-name>
  ```

- [ ] If the user declined a worktree, omit `--worktree`. Never silently fall
  back to the main checkout after worktree creation fails.
- [ ] Enter the reported worktree before running workflow commands.
- [ ] Persist the active `playbook_id`, confirmation contract, and reactive
  handoff policy in `.cafe/issues/<issue-name>/issue.yaml` in the active
  checkout before the first `cafe make`:

  ```yaml
  playbook_id: default
  confirmation_contract:
    user_required: [spec, plan]
    driver_confirmable: []
    confirmed_by: user
    confirmed_at: 2026-07-16
  reactive_user_handoffs:
    need_clarification: user_required
    need_permission: user_required
    alignment_checkpoint: driver_resolvable_when_clear
  ```

- [ ] Re-run `cafe playbook confirmation-gates <playbook-id>` and verify the
  locale and exact candidate partition before the first `cafe make`.
- [ ] If preparation accidentally omitted an approved worktree, repair or
  recreate preparation before the first `cafe make`; do not discard the issue
  configuration or silently continue in the main checkout.

A legacy `mandate.confirmation_contract.agent_confirmable` value in strategic
context is only a kickoff proposal. Rename it to `driver_confirmable`, compare it
with the active playbook, and obtain fresh confirmation before persisting it.
