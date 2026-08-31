# Runtime And Catalog Preflight

Read this reference before a new kickoff is rendered and before resuming a
stale kickoff contract. Run both trusted, read-only checks from the canonical
repository or active linked worktree:

```bash
cafe update check --json
cafe catalog check --json
```

The catalog command compares intentional project entries across playbooks,
phase skills, and agents against their Global destinations. It resolves the
canonical repository plus active worktree overlay and reports content-bound
digests without copying fallback entries into the project.

## Route the check results

- An update status of `unavailable` must be recorded and clearly warned about,
  but it must not be described as current and kickoff continues with the
  installed version.
- An empty catalog difference list means identical content or no project
  entries are eligible. Stay silent and do not ask a catalog question.
- A catalog status of `over_budget` is an explicit incomplete preflight, not a
  no-difference result. Record and report the declared entry limit, do not ask
  for publication approval, and narrow the catalog check with `--kind` or
  `--entry` before kickoff continues.
- When catalog differences exist, show one bounded report covering all three
  catalog kinds and ask one combined catalog decision for the exact selected
  entry IDs.
- A runtime update and a catalog publication are separate approval scopes.
  Never infer either approval from kickoff confirmation, a generic `continue`,
  or approval of the other scope.

Persist each check's timestamp, status, installed/latest versions when
applicable, comparison token, effective catalog digests, decision, and any
post-change evidence in the active issue's `preflight` mapping. Reuse a prior
decision only when the complete bound token is unchanged.

## Apply only an exact approval

Use the token and selection the user approved:

```bash
cafe update apply --token <token-from-update-check> --json
cafe catalog sync-global --token <token-from-catalog-check> \
  --approve playbook:<name> \
  --approve phase:<name> \
  --approve agent:<role>/<name> \
  --json
```

Do not run either apply command when that scope was declined. Catalog
publication flows only from the effective project view to matching Global
paths; it does not modify project content or CLI-native helper-skill installs.
`cafe skill sync-global` remains a separate helper installation command.

After an approved change, re-run both read-only checks and record the fresh
results. Compare the effective workflow digests with the pre-change evidence.
If effective behavior changed, re-render and reconfirm the kickoff contract
before preparation, start, or resume. If it did not change, retain the
post-change evidence and continue under the already confirmed contract.
