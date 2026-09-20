# Artifact contracts

CAFE workflows communicate through declared artifacts. A step must publish the
artifact named by its playbook entry, and a consumer must list every artifact it
uses in `input_artifacts`. Code-oriented workflows also publish a separate
verified workspace companion; the human-readable development document and the
Git identity are intentionally independent records.

## Document artifacts

The runtime writes one `artifact.json` beside each published step output. The
record contains the logical name, kind, positive version, producing step, and
path to the authoritative output. The output file remains the semantic source;
the JSON record is a durable pointer used by the blackboard and rebuild logic.

Do not infer an artifact from a phase or role name. Custom playbooks may use
names such as `summary_doc`, `review_doc`, or `verified_snapshot`; declarations
are the only source of the relationship.

## Verified workspace companions

A producing step opts in with a declaration such as:

```yaml
output_artifact: code
workspace_artifact: workspace
```

The runtime writes `workspace.json` from the current committed Git state after
confirming that the worktree is clean. A version-one workspace record contains:

- `repository`: the active worktree root;
- `base_sha` and `head_sha`: canonical full commit IDs, with the base reachable
  from the head;
- `changed_files`: the exact `git diff --name-status --find-renames` result;
- `name`, `version`, and `schema_version`.

The workspace version is stored separately from the document version. A
consumer must verify the workspace against its active repository before using
it. Verification rejects a different repository, stale head, dirty worktree,
changed-file drift, unsupported schema, or contradictory Git identity. A stale
workspace must be republished; consumers must not silently fall back to a
different commit or reconstruct the snapshot from prose.

Review, QA, and PR steps should declare the companion as an input when the
playbook produces it:

```yaml
input_artifacts: [spec, code, workspace, review_feedback]
```

`workspace` is the authoritative Git and changed-file identity. `code` is the
readable summary or implementation artifact used for context.

## Correction routes and Todo identity

Correction edges are declared by destination, not by built-in phase names:

```yaml
behavior:
  feedback_routes:
    receiver:
      artifact: review_doc
      source_kind: editorial_review
      todo_source: editorial_review
      todo_id_prefix: REV
```

The route artifact must be the producer's `output_artifact`, and the receiver
must declare it in `input_artifacts`. The source must use the canonical Todo
heading and rows, with the declared source and ID prefix. On retry, CAFE uses
the persisted inbound edge and the latest complete declared source; it does
not widen the scope from unrelated feedback or chat history.

## Verification receipts

`cafe verification` is an optional command runner that can preserve an
iteration-local execution log and receipt. For example:

```bash
cafe verification run \
  --output-file .cafe/issues/<issue>/<step>/iteration_<n>/output.md \
  --scope targeted -- <test command>
```

The resulting `verification.json` binds the command, exit status, Git head,
and scope. It can be checked or reused when a user or another explicitly
declared process wants that provenance.

Ordinary Todo completion, review, and workspace publication do not require a
CAFE verification receipt. `Targeted evidence` in a Develop ledger is optional
informational text and is not validated against a receipt. Files, commits, Todo
identity, and clean-worktree checks remain authoritative independently.

## Compatibility

Blackboard rebuild continues to read existing `artifact.json` records and
legacy scalar artifact pointers. New workspace companions use the explicit
version-one schema. Unsupported future workspace schemas fail closed instead
of being interpreted as an older snapshot.
