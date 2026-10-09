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

## Rate-limit restart policy

The issue-scoped `execution.rate_limit_restart_policy` setting accepts exactly
`continue_last_success` (the legacy default) or `recheck_priority`:

```sh
cafe settings update ISSUE --set 'execution.rate_limit_restart_policy="recheck_priority"' --preview --json
cafe settings update ISSUE --set 'execution.rate_limit_restart_policy="recheck_priority"'
```

An absent setting preserves existing behavior without rewriting issue state. Preview
is read-only; identical saves are no-ops. A valid save changes only this setting in
`issue.yaml`, preserving other configuration, tasks, artifacts, sessions and authority.
Invalid values, including `null`, fail before writes or provider execution.

After a current, typed rate-limit interruption, `recheck_priority` offers
`retry_configured_order` in the existing interrupted-execution HumanTask. Choosing it
retries the same step and iteration from the **currently configured** primary CLI/model
in a new provider session. If primary remains limited, the existing retry delays and
configured fallback order apply. No extra provider or health probe is added. The order
and effective models are resolved once for the invocation and shared by previews and
execution. A successful fallback can become sticky again; replay and corrections do
not reapply a consumed priority reset. Single-entry chains propagate their failure.

Saving the setting never answers a task or starts execution. Enabling it while an older
rate-limit task is pending offers a new, explicitly superseding task at the next workflow
boundary. The old declaration remains in history; cancelled tasks reject later results.
Until supersession, old tasks remain answerable under their original declared choices.

`retry` explicitly preserves the interrupted CLI/session. `retry_fresh_session` keeps its
existing provider/model selection semantics. Both are visible overrides of the saved
recheck strategy for that invocation and leave the setting unchanged. Configured-order
recovery may incur the primary's existing rate-limit delays before fallback and gives up
its prior provider conversation; reconstruct work from the saved workflow artifacts.
Unrelated interruptions, same-invocation retries and earlier rate limits followed by
success do not qualify. Configuration changes invalidate incompatible sticky snapshots.

Inspect `restart_diagnostics` in the owning step's `iteration.json` for saved/effective
policy, eligibility/reason, human decision/task/result IDs, configured/effective CLI/model
orders, sticky disposition and override reason. `restart_recovery_consumption` correlates
the selected task/result, interruption and starting invocation. Existing `failed_attempts`
and actual successful CLI/model remain separate evidence; diagnostic excerpts are
sanitized. A human retry admits its selected CLI/chain's constraint applicability while
preserving the gate for changed constraint definitions, tools or workload boundaries.
