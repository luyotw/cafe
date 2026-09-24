# Targeted workflow settings updates

CAFE can preview or save one supported workflow-scoped setting without rebuilding or
reconfirming the complete Driver contract:

```sh
cafe settings update ISSUE --set 'driver={"mode":"event-driven","clis":[{"cli":"claude"}]}' --preview --json
cafe settings update ISSUE --set 'pr.auto_create=false'
```

`driver` must be the complete Driver object. Event-driven chains use an unpinned primary
CLI and may include fallback entries with schema-valid models. `pr.auto_create` must be a
JSON Boolean and is accepted only when the issue's effective playbook requests
`cafe.pr.publish`. A command accepts exactly one owner, so mixed Driver and PR batches are
rejected before writing.

Driver contract schemas 3 and 4 are supported as inputs and remain in their original
version after an update. The update preserves the existing identity and confirmation
provenance, recalculates the current semantic digest, and advances the existing revision
chain. It does not invent v4 delivery data for a v3 document. Preview validates and reports
the proposed value without creating locks or rewriting files; an identical apply is also a
no-op.

Saving a setting only atomically replaces its owner's authority file: Driver settings in
`driver/contract.json`, or PR choice in the authoritative `issue.yaml`. It does not invoke a
provider, publish a PR, change global defaults, resume a workflow, or modify dispatch,
session, checkpoint, event, or artifact state. The command output describes only the value
proposed, saved, or already unchanged; it is not a downstream activation guarantee or an
update-history record.
