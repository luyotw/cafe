# Runtime And Catalog Preflight

Read this reference before a new kickoff is rendered and before resuming a
stale kickoff contract. Run both trusted, read-only checks from the canonical
repository or active linked worktree:

```bash
cafe update check --json
python3 <skill-dir>/scripts/catalog_version_check.py
```

The script directly invokes `cafe catalog check --json` with an argv list. On a
successful check it returns the raw report under `catalog_check` and a
language-neutral `content_mismatch_entry_ids` list. It does not render reminder
prose. The underlying catalog command compares intentional project entries
across playbooks, phase skills, and agents against their Global destinations.
Only unwrap those two fields when the script exits zero. On a nonzero exit, the
script deliberately returns the raw catalog stdout, stderr, and exit code
without a wrapper; do not read nested keys or reminder IDs. Route that raw
result through the existing catalog preflight handling, including the
`over_budget` rules below.

## Route the check results

- An update status of `unavailable` must be recorded and clearly warned about,
  but it must not be described as current and kickoff continues with the
  installed version.
- An empty `content_mismatch_entry_ids` list means no reminder. Stay silent and
  do not ask a catalog question.
- A catalog status of `over_budget` is an explicit incomplete preflight, not a
  no-difference result. When `discovery_complete` is true, record its complete
  bounded `affected_entry_ids` and effective digests without asking a
  publication question or reminder. If `discovery_complete` is false, stop the
  preflight with the reported hard-limit error instead of presenting a partial
  result.
- `missing_global` is an ordinary project-only entry and never appears in the
  mismatch list. Only entries already classified by CAFE as `content_mismatch`
  appear. When that list is non-empty, use the effective conversation locale to
  append one non-blocking recommendation with those exact IDs at the very end
  of the kickoff contract. State that kickoff confirmation does not approve
  publication and that synchronization may be requested separately.
- A runtime update and a catalog publication are separate approval scopes.
  Never infer publication approval from the kickoff confirmation, its catalog
  reminder, a generic `continue`, or approval of the runtime-update scope.

Persist each check's timestamp, status, installed/latest versions when
applicable, comparison token, effective catalog digests, decision, and any
post-change evidence in the active issue's `preflight` mapping. The reminder
list is transient and is not persisted. Record `not_requested` when no explicit
publication request exists; project-only entries do not require a decline
decision.

A changed comparison token invalidates its cached decision, but does not by
itself show a semantic change or require kickoff reconfirmation. Re-run the
check and perform a bounded semantic comparison of the effective confirmed
contract and execution behavior. Ordinary start or resume checks use
`cafe catalog check --json` directly and never display a synchronization
reminder. Run the reminder script only while rendering a complete new or stale
kickoff contract. Reconfirm only for a contract or observable-behavior change,
or a material runtime, dependency, or permission difference. Verified
metadata-only churn may continue after recording the classification and
evidence. If the difference cannot be shown to be non-semantic, fail closed.

## Apply only an explicitly requested, exact approval

Do not initiate catalog publication from preflight. Only after the user
separately requests synchronization, use the fresh token and exact selection
the user approved:

```bash
cafe update apply --token <token-from-update-check> --json
cafe catalog sync-global --token <token-from-catalog-check> \
  --approve playbook:<name> \
  --approve phase:<name> \
  --approve agent:<role>/<name> \
  --json
```

Do not run either apply command without that explicit request. Catalog
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
confirmation stop. When it finds a material difference, re-render and reconfirm
the kickoff contract; otherwise retain the post-change evidence and continue
under the confirmed contract.

For a Driver-managed issue, that confirmed contract is the one issue-scoped
`driver/contract.json` authority. Cache files, raw source digests, labels,
timestamps, and comparison tokens are diagnostics: an identity change makes
them stale and requires fresh checks, but does not by itself rewrite the
contract or force reconfirmation. Only a proved semantic/material difference
does so; incomplete evidence fails closed.
