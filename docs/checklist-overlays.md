# Checklist overlays

A step retains one primary skill and can inject independent checklist policies.
Only an injected skill's explicit `workflow.checklist_overlay` contributes gates;
its ordinary `workflow.checklist` is not automatically activated. An overlay
never grants tools or other execution authority.

For example, add a small policy to the workflow skill environment:

```yaml
skills:
  workflow:
    shared: [review-policy]
```

Place this policy in `.cafe/skills/review-policy/SKILL.md`:

```yaml
---
name: review-policy
description: Require review of the current work
workflow:
  checklist_overlay:
    context_references:
      review_scope: scope.md
    variants:
      - when: {feedback: true}
        sections: [{reference: correction.md}]
      - sections: [{reference: review.md}]
---
Follow the applicable checklist gates.
```

Create `references/scope.md`, `references/review.md`, and
`references/correction.md` in that same skill directory. For example, `scope.md`
can contain `the current output at {output_file}`, and `review.md` can contain:

```markdown
[ ] Review {review_scope} and resolve findings before successful handoff.
```

This is the same explicit declaration exercised by
`tests/integration/test_checklist_overlay_workflow.py`. It works with the real
Develop skill and with custom primary skills and artifact names.

## Selection and ownership

Each contributor independently selects its first matching variant using one
iteration, feedback, and artifact snapshot. An omitted outer `when` is
unconditional. An outer condition can restrict applicability:

```yaml
checklist_overlay:
  when: {min_iteration: 2}
  variants:
    - sections: [{reference: review.md}]
```

An inactive overlay adds no gates. An applicable overlay without a matching
variant is an error identifying the step, source skill, and declaration field.
All declared required reference files are checked even in inactive variants
and unselected primary skill alternatives. `optional_checklist` retains its
optional-file behavior. Inactive overlays still contribute their required
tools, inputs, human tasks, and execution profiles through the existing
workflow contract resolver.

Rendering order is primary, then the resolved shared/role/step contributions,
then primary-controlled role guidance. Existing `extend`/`replace` and source
de-duplication apply. Repeating the primary or an injected skill does not add it
again. A primary unconditional variant cannot hide an overlay.

References and local context fragments resolve from the declaring skill's
selected root, including project overrides. Different skills can use the same
filenames and local placeholder names. Each contributor sees common inputs
and its own locals; locals are not exported to the global prompt. Local names
cannot shadow runtime values, primary prompt references, or any contributor's
common input, regardless of declaration order. Missing required inputs and
unresolved selected overlay instructions fail with source context.

An overlay can request an existing `template_catalog` section; the primary
owns the catalog and settings. Overlay declarations cannot contain guidance,
global prompt reference, or template ownership settings. A legacy/custom
primary retains its fallback checklist, and applicable overlays are appended
before its existing role guidance. No-overlay output keeps its established
format.

## Todo evidence

Every selected `todo_projection` section participates in completion. When
there are overlays, checklist and `## Todo Progress` ledger entries use a
consumer-local handle such as `TASK-001__0123456789abcdef`. Copy the actual
handle and source fingerprint from the generated checklist or retry guidance;
do not construct the suffix manually.

The original producer ID and fingerprint stay unchanged in the authoritative
artifact. Projecting one producer item from multiple contributors or sections
creates distinct consumer handles. Each needs a separate completed ledger
entry and valid evidence. Entries may cite the same valid commit where it
covers their files, but one entry cannot close another handle. Without
overlays, existing item IDs and ledger formatting remain unchanged.

Before success, CAFE re-resolves every projected source and compares its path,
version, content, IDs, and fingerprints, then checks current file/commit
evidence and worktree cleanliness through the existing evidence validator.
The materialized snapshot does not freeze evidence validity.

## Completion and recovery

The iteration's `effective_checklist` metadata records the complete rendered
gates and source bindings. Successful baton and legacy-status exits must
satisfy the full effective checklist. Deleted, reordered, duplicated, or
rewritten required gates cannot authorize completion. Retry and missing-file
rebuild use the same contributor declarations. Clarification, permission, and
manual handoff remain available with their existing independent output
contracts.

Automatic no-change continuation also validates the gates. After a completed
no-change HumanTask with overlays, execution returns to the owning step to
finish or revalidate its gates and evidence, then follows the already declared
decision target. The durable decision remains completed and is not asked again.
This return also recovers missing or damaged checklist metadata.

Resume preserves completion only for unchanged source/condition/item identities
with valid evidence. Complete item blocks include continuation rules. Identical
text from different contributors does not transfer a checkmark. Changed source
bindings or content reopen the affected gates. Missing, corrupt, or incompatible
overlay metadata conservatively reopens completion; unambiguous no-overlay
legacy restoration retains its compatibility behavior.

Gate identity is not proof that a reviewer executed, nor an attestation that an
approval is fresh. Overlays add authored requirements to existing workflow
validation; they add no reviewer authority, scheduling language, or trusted
capability grant.
