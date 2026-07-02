---
name: alignment
description: Format a policy-produced alignment checkpoint payload for user review.
---

# Alignment Checkpoint Formatter

Use this skill only to make an existing alignment checkpoint payload clearer.
The deterministic policy result is authoritative.

## Guardrails

- Do not decide whether alignment is required.
- Do not downgrade, skip, or auto-approve a policy-required checkpoint.
- Do not treat alignment as approval for host-side capability execution.
- Preserve all triggered rules, affected documents, risks, assumptions, and allowed decisions.

## Output Shape

When asked to format a checkpoint, include:

- interpreted goal
- proposed scope
- non-scope
- triggered rules and risk level
- affected strategic documents
- risks and assumptions
- strategic document update recommendation
- exact user decision requested
- recommended resume target

If the payload says strategic documents must be updated first, make that block obvious and keep the workflow paused until the user updates guidance, narrows scope, or explicitly rejects/defer the work.
