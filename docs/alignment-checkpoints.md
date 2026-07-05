# Alignment Checkpoints

Alignment checkpoints pause a workflow before selected agent steps when policy says the work may affect product direction, roadmap scope, governance, principles, positioning, strategic context, user trust, or external mutation boundaries.

They are separate from clarification. Clarification asks for missing facts. Alignment asks whether the intended direction and tradeoffs still match higher-level guidance.

## Policy Levels

- `must_align`: stop before agent execution and hand off to the user with `intent=alignment_checkpoint`.
- `alignment_note`: record a structured blackboard event and continue.
- `no_alignment`: continue normally.

The decision is deterministic. Agents or formatting skills may add evidence and clearer wording, but they cannot downgrade a policy-required checkpoint.

## Payload Files

Required checkpoints write:

- `.cafe/issues/<issue>/<step>/iteration_NNN/alignment_request.json`
- `.cafe/issues/<issue>/<step>/iteration_NNN/strategic_document_update_request.json` when a strategic document update is required

The request includes the interpreted goal, proposed scope, non-scope, triggered rules, affected documents, risks, assumptions, strategic document update recommendation, requested decision, allowed decisions, and a fingerprint.

## User Decisions

Supported outcomes are:

- approve and continue
- narrow scope and continue
- revise the specification
- revise the plan
- update strategic documents first
- strategic documents updated
- pause for manual decision
- reject or defer

Approval resumes the original step only when no required strategic-document update remains unresolved. Narrowing writes correction text back to the blocked step and forces the policy gate to evaluate again. Revising spec or plan routes the workflow to that step.

## Strategic Documents

The gate reads `.cafe/strategic_context.yaml` and records configured document paths, statuses, and hashes. Missing or draft documents are surfaced when relevant.

If a required update is identified, execution stays paused until one of these happens:

- the affected document changes
- scope is narrowed so the update is no longer required
- the user explicitly rejects or defers the work

## Non-Interactive Runs

Plain `--user-input` text never approves an alignment checkpoint.

Use an explicit JSON payload:

```json
{"decision":"approve"}
```

```json
{"decision":"narrow","correction":"Keep this to technical plumbing; do not change roadmap scope."}
```

```json
{"decision":"update_strategic_documents_first"}
```

```json
{"decision":"strategic_documents_updated","user_confirmed":true,"user_confirmation":"User confirmed the final strategic document content."}
```

`strategic_documents_updated` requires confirmation evidence in non-interactive
runs unless the update is mechanically copied from already confirmed strategic
material.

If the payload is missing or invalid, the workflow remains paused at `user`.
