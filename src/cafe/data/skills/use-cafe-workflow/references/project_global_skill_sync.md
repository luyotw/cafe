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
  no-difference result. When `discovery_complete` is true, its single
  `affected_entry_ids` set is complete within the reported hard discovery
  limit. Present that set in one bounded decision, then run one exact combined
  `--entry` scope for the approved selection (up to the reported entry limit)
  to obtain its content-bound token. The exact token also binds the complete
  discovery scope. The Driver must not paginate, repeat whole-catalog scans, or
  treat the discovery token as publication approval. If `discovery_complete`
  is false, stop the preflight with the reported hard-limit error instead of
  presenting a partial decision.
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

A changed comparison token invalidates its cached decision, but does not by
itself show a semantic change or require kickoff reconfirmation. Re-run the
check, handle only any separately scoped action it reports, and perform a
bounded semantic comparison of the effective confirmed contract and execution
behavior. Reconfirm only for a contract or observable-behavior change, or a
material runtime, dependency, or permission difference. Verified metadata-only
churn such as paths, timestamps, caches, or labels may continue after recording
the classification and evidence. If the difference cannot be shown to be
non-semantic, fail closed.

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

## Helper installation and publication

Observational startup paths (`status`, `show`, checks, lists, help, and workflow
dry runs) do not write global helper directories or synchronization metadata.
Eligible mutating commands may install only missing helpers. Released packages
use their packaged bundle, while linked Git worktrees resolve the canonical main
checkout bundle; an existing directory or symlink is never repaired or replaced
by startup.

Updating an existing CLI-native helper requires an explicit
`cafe skill sync-global`. That command reports the exact resolved bundled source
even when every destination is unchanged, and reports installed, updated,
unchanged, or failed status per destination. Feature-worktree content is not
published globally unless the user deliberately invokes this separate command.
Catalog approval does not grant helper-publication approval.

After an approved change, re-run both read-only checks and record the fresh
results. Compare the effective workflow digests with the pre-change evidence.
Digest changes trigger the bounded semantic comparison above, not an automatic
confirmation stop. Re-render and reconfirm the kickoff contract when it finds a
material difference; otherwise retain the post-change evidence and continue
under the confirmed contract.
