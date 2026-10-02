# Targeted workflow settings updates

CAFE can preview or save one supported workflow-scoped setting without rebuilding or
reconfirming the complete Manager contract:

```sh
cafe settings update ISSUE --set 'manager={"mode":"event-driven","clis":[{"cli":"claude"}]}' --preview --json
cafe settings update ISSUE --set 'pr.auto_create=false'
```

`manager` is the canonical owner and accepts a complete Manager object. The former
`driver` owner remains a compatibility input for existing consumers and records. New
workflows write Manager state to `manager/contract.json`; an existing Driver workflow
continues under its original authority. Retained aliases are compatibility-only and may
be removed after consumers have a supported transition.

Event-driven chains use an unpinned primary CLI and may include fallback entries with
schema-valid models. `pr.auto_create` must be a JSON Boolean and is accepted only when the
issue's effective playbook requests `cafe.pr.publish`. A command accepts exactly one owner,
so mixed Manager and PR batches are rejected before writing.

New Manager contracts use schema version 8. Existing Driver contracts in versions 3–7
retain their original validation, digest, confirmation evidence, and authority during
continuation. A rename alone does not re-confirm or rewrite that evidence. If a legacy
record cannot be shown equivalent to a current name or path, the operation stops and
identifies the affected record for recovery.

A Manager update atomically replaces the Manager authority file. A legacy Driver update
continues to use its original Driver authority file. Preview validates and reports the
proposed value without creating locks or rewriting files; an identical apply is also a
no-op. Settings updates do not invoke a provider, publish a PR, change global defaults,
resume a workflow, or modify dispatch, session, checkpoint, event, or artifact state.
