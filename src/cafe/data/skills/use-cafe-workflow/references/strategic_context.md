# Strategic Context And Authority

Use `.cafe/strategic_context.yaml` as the one project-root file for strategic
documents and decision authority. Read it and only the relevant linked
documents before kickoff decisions, driver-confirming outputs, answering
workflow questions, final PR review, or merging.

Do not split this information into `mandate.yaml` or another parallel config.

## Document inventory

| Category | What it answers | Example paths |
| --- | --- | --- |
| Product direction | What is being built, priorities, boundaries | `docs/roadmap.md` |
| Company positioning | Audience, positioning, non-goals | `docs/positioning.md` |
| Department norms | How the team operates | `CONTRIBUTING.md`, `docs/guidelines/*.md` |
| Playbook policy | Rules for this workflow type | `docs/policies/<name>.md` |

If a needed category is `missing`, do not start workflow execution. Interview
the user, draft the document, obtain confirmation, save it at the agreed path,
then mark it `exists` or user-approved `draft`.

## Authority model

- `documents`: repository-wide strategic grounds and their status.
- `mandate`: repository-wide default authority.
- `issues.<name>`: optional, protected overrides created only at the user's
  explicit request.

Strategic context is not playbook configuration. Do not add `playbook_id` to
`mandate` or `issues.<name>`, and do not store a selected playbook anywhere in
this file. A legacy playbook field is non-authoritative and must not be copied
or refreshed; leave cleanup to an explicit user request.

Levels:

- `agent`: decide within confirmed documents and issue artifacts;
- `propose`: make a grounded recommendation and continue only as the playbook
  permits;
- `escalate`: stop for the user.

Example schema:

```yaml
version: 1

documents:
  roadmap:
    path: docs/roadmap.md
    status: exists          # exists | draft | missing
  positioning:
    path: docs/positioning.md
    status: missing
  engineering_guidelines:
    path: CONTRIBUTING.md
    status: exists

mandate:
  preset: technical-led
  axes:
    product_scope:
      level: escalate
      grounds: [roadmap, positioning]
    technical:
      level: agent
      grounds: [engineering_guidelines]
    quality:
      level: agent
  out_of_mandate:
    - pricing
    - production deploy approval
  notes: |
    Default for this repo. User confirmed 2026-05-23.

# Optional and protected. Include only after an explicit user request.
# issues:
#   issue301:
#     axes:
#       product_scope: {level: escalate}
#       technical: {level: agent}
#     notes: |
#       This issue only: stay within v0.2 roadmap scope.
```

## Protected issue overrides

- Do not create `issues.<issue-name>` because the current task appears narrower
  than the repository default.
- Do not store workflow progress, baton state, phase outputs, review notes, or
  temporary scope summaries under `issues:`.
- Do not add, edit, or remove an issue override unless the user explicitly asks.
- If the issue appears to need different authority, ask before writing the
  override. Otherwise keep the repository mandate and classify deltas normally.

## Applying authority

Resolve an explicit issue override over `mandate`, then ground the decision in
the named documents and latest accepted issue artifacts.

- For questions: classify by axis and level. A contradiction or extension of
  strategy requires escalation. Missing grounds require document co-creation;
  do not invent strategy.
- For driver-confirming spec or plan: verify completeness, mandate, and
  consistency with accepted upstream artifacts.
- For PR review: create blocking findings only for in-mandate axes backed by
  `exists` or user-approved `draft` documents.
- Merge, close, and `cafe close` only after all such blockers are resolved.

Write repository-wide `documents` and `mandate` updates during kickoff only as
confirmed, and never include a playbook selection in those updates.
Leave `issues:` untouched unless the user explicitly requested an issue-specific
strategic override.
