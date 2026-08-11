---
name: use-cafe-workflow
description: Use this skill when you need to develop an issue by driving CAFE from the terminal with non-interactive commands, including bounded diagnosis and declarative repair when the workflow behaves incorrectly.
version: 1.13.0
---

# Use CAFE Workflow

## Purpose

- Drive CAFE through spec, plan, develop, review, and PR without bypassing its
  artifacts, blackboard state, or baton handoffs.
- Keep driver decisions grounded in the confirmed kickoff contract and
  `.cafe/strategic_context.yaml`.
- Prefer non-interactive commands so work can run unattended and resume cleanly.
- Diagnose abnormal workflow behavior only far enough to choose the correct,
  safe repair layer.

## Progressive disclosure

Read this file completely, then load only the references required by the current
situation. Resolve every path relative to this `SKILL.md`.

| Situation | Read before acting |
| --- | --- |
| Start or resume before the first `cafe make`; answer a locale or kickoff question | `references/kickoff.md` and `references/strategic_context.md` |
| Run, resume, inspect, retry, or recover ordinary workflow work | `references/running_workflow.md` |
| Handle `to_owner=user`, confirmation, clarification, permission, or alignment | `references/handoffs_and_alignment.md`; also read `references/strategic_context.md` |
| Start or resume linked work; confirm a spec or plan with an issue-decomposition assessment | `references/issue_decomposition.md`; also read `references/strategic_context.md` and `references/handoffs_and_alignment.md` |
| Diagnose incorrect workflow behavior or choose a repair layer | `references/diagnosis_and_repair.md`; also read the relevant runtime reference above |
| Review or ship after the PR phase | `references/convergent_pr_review.md`; also read `references/strategic_context.md` |
| Measure fresh-versus-resumed correction efficiency | `references/correction_ab_experiment.md` |

If more than one situation applies, read every listed reference before acting.
Do not preload unrelated references.

## Core invariants

- The complete kickoff contract is the first blocking gate. Do not run
  `cafe prepare`, mutate the repository, or run the first `cafe make` before the
  user confirms it.
- Resolve the effective conversation locale from the active playbook, unless the
  user directly overrides it for the thread. Use that locale for every
  driver-to-user message.
- Use `.cafe/strategic_context.yaml` as the single source for strategic
  documents and authority. Do not invent strategy or silently create issue
  overrides.
- Treat planned output confirmation, reactive user handoffs, and semantic
  alignment as separate decisions. The driver owns alignment; phase agents do
  not approve themselves.
- Validate issue-decomposition assessments before confirming spec or plan;
  coordinate any authorized split through existing authority boundaries and
  reconstruct linked-work position from durable records.
- Prefer `cafe make`. Use a focused `cafe workflow --execute --start-step
  <step>` only for a bounded retry or diagnosis.
- Do not manually edit workflow artifacts, blackboard state, or
  `next_step.txt` except when repairing confirmed broken workflow state.
- Do not bypass CAFE by directly asking an agent to implement an issue that the
  user asked CAFE to run.
- Modify source-of-truth playbooks and phase skills, never generated artifacts
  or installed global copies. Driver and CAFE core defects require escalation
  unless the user explicitly authorizes that source change.
- A phase or PR reporting success is evidence, not final proof. Ship only after
  the independent driver review has no unresolved in-mandate blockers.

## Driver checklist

### Start or resume

- [ ] Read the kickoff and strategic-context references.
- [ ] Resolve the active playbook, effective locale, confirmation gates,
  reactive handoffs, mandate, and worktree behavior.
- [ ] Present the deterministic kickoff table and obtain explicit confirmation.
- [ ] Check Git state, initialize CAFE if needed, prepare the issue, enter the
  recorded worktree, and persist the issue-owned contract.

### Run

- [ ] Read the running-workflow reference.
- [ ] Start with the user's requirement or resume with `cafe make`.
- [ ] Inspect progress through `cafe status` and `cafe show`; consult the
  blackboard only when command output is insufficient.
- [ ] When CAFE pauses, classify the handoff before supplying any input.
- [ ] When behavior is wrong, stop normal execution and use the bounded
  diagnosis reference.

### Complete

- [ ] Confirm the terminal state is `Workflow completed ... next=done`.
- [ ] Read the convergent PR review reference and finish its full review matrix.
- [ ] Merge only after all blockers are resolved, close the linked issue, run
  `cafe close`, and confirm the issue is absent from `cafe ls`.
- [ ] Report the relevant test evidence and final state in the effective locale.

## Reference index

- `references/kickoff.md` — locale, confirmation contract, formatter, prepare.
- `references/strategic_context.md` — documents, mandate, protected overrides.
- `references/running_workflow.md` — commands, inspection, retries, operating rules.
- `references/handoffs_and_alignment.md` — user pauses and driver decisions.
- `references/diagnosis_and_repair.md` — bounded classification and disposition.
- `references/convergent_pr_review.md` — batched final review, merge, close, teardown.
- `references/correction_ab_experiment.md` — controlled efficiency experiment.
- `references/issue_decomposition.md` — assessment validation, authority,
  delivery gate, and durable project position.
