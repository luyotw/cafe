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

A changed comparison token invalidates reuse of its cached decision, but it is
not by itself evidence that workflow behavior changed and must not by itself
cause a kickoff reconfirmation. Re-run the check and classify the bounded delta:

- Ask only for the exact separately scoped decision exposed by the fresh
  result: applying an available runtime update or publishing reported catalog
  differences. A fresh `current`, `identical`, or `no_project_entries` result
  needs no user decision.
- Compare the resolved playbook graph and gates, phase-skill instructions and
  execution profiles, runtime entrypoint and dependency environment, and other
  rendered kickoff inputs. Reconfirm when that semantic comparison changes the
  confirmed contract, identifies any material runtime or dependency-environment
  difference, or changes observable execution behavior. A material environment
  difference requires reconfirmation before a probe happens to expose a failure.
- Treat absolute checkout paths, canonical-plus-worktree root enumeration,
  timestamps, caches, generated `__pycache__`, and a version label alone as
  diagnostic metadata, not semantic change. Treat a file-mode difference as
  non-semantic only after verifying that it changes no effective read, write,
  or execute access for any runtime actor; otherwise fail closed and classify
  it as semantic. When a runtime label differs, verify the interpreter,
  imported source, dependency environment, required command preview, and
  selected model probes before classifying it. Only verified label or path
  noise may continue without reconfirmation.

Record the classification and evidence. Do not ask the user to reconfirm an
unchanged contract merely to acknowledge diagnostic noise.

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
Digest changes trigger the bounded semantic comparison above rather than an
automatic confirmation stop. Re-render and reconfirm the kickoff contract
before preparation, start, or resume when that comparison finds a contract
change, a material runtime or dependency-environment difference, or changed
observable execution behavior. Otherwise retain the post-change evidence and
continue under the already confirmed contract without another user
confirmation.
