# Issue Decomposition Assessment Contract

Use this stable contract whenever requirements or planning assesses whether an
issue should remain whole. It is a recommendation, not authority to mutate
project records.

## Assessment structure

```markdown
## Issue Decomposition Assessment

- Decision: `keep` or `split`
- Rationale: [evidence for the recommendation]
- Current issue scope: [independently acceptable outcome to deliver now]
- Trigger: [none, product scope, or implementation scope]

### Proposed follow-up issues

| Title | Goal | Depends on | Scope boundary | Non-goals | Definition of Done |
| --- | --- | --- | --- | --- | --- |
```

For `keep`, state why the issue remains a cohesive, reviewable outcome and use
`none` for the trigger. For `split`, retain a useful, independently acceptable
outcome in the current issue and propose non-overlapping follow-up outcomes.
Every follow-up must state its outcome, dependency, scope boundary, non-goals,
and Definition of Done; reject vague or unsupported proposals.

## Role boundaries

- Requirements assesses whether the request contains independently acceptable
  product capabilities, scopes, or confirmation cycles. It must not split a
  normal medium feature merely because it has several implementation parts.
- Planning assesses whether the confirmed scope can safely be delivered,
  tested, and reviewed using repository evidence. It may refine dependency
  order but must not silently change confirmed product scope.
- Phase agents recommend only. They never create issues, update roadmaps, change priority
  or scheduling, or make external project mutations. The
  driver validates and coordinates an approved proposal through existing
  authority and confirmation boundaries.
