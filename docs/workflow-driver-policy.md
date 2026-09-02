# Workflow Driver Policy

CAFE workflow-driver contract version 2 records who owns decisions. It does
not record how many steps one command runs, whether a local process runs in the
foreground or background, or whether a delegated CLI is expected to be
available.

## Policy forms

Every issue using version 2 stores `contract_version: 2` and exactly one flat
driver form:

```yaml
# The initiating conversation or CLI retains boundary decisions.
contract_version: 2
driver:
  mode: attached
  poll_interval_seconds: 180
```

```yaml
# Eligible work continues without an agent driver.
contract_version: 2
driver:
  mode: unattended
```

```yaml
# A dedicated CAFE-owned driver handles substantive boundaries.
contract_version: 2
driver:
  mode: delegated
  cli: codex
  model: gpt-5.6-codex
```

The policy rejects missing or extra fields, nested legacy groups,
`driver_execution`, execution or advancement settings, hosting, availability,
aliases, and provider-default delegated models. CAFE never migrates or infers
those values. An existing issue can receive v2 only through one complete
explicit `cafe update-driver-policy` request.

## Runtime behavior

- Attached executes one eligible phase and returns responsibility to the
  initiator. Its positive polling cadence reads durable state only; polling
  cannot execute work, create a delegated boundary, or consume authorization.
- Unattended continues eligible phases until a HumanTask, confirmation,
  permission need, error, explicit stop, or completion.
- Delegated alone creates driver packets and decisions. The selected CLI always
  receives the exact model on acquisition and resume. Acquisition, resume,
  validation, or reported-model mismatch failures pause durably without
  falling back to unattended or reusing the initiating conversation.

Manual `--single-step` and local foreground/background worker selection are
invocation controls. They are not policy fields and do not change who owns a
boundary. Existing worker leases prevent concurrent local advancement; this is
not a supervisor or stale-run repair service.

`cafe workflow --execute --background` resumes an already prepared workflow
through the fixed background worker. Foreground execution and that worker both
hold and renew the same durable advancement lease while the runtime is active.

## Durable state and inspection

The blackboard records lifecycle state, correlated packets and decisions,
one-time decision consumption, and delegated CLI/model/session provenance.
Status and show commands expose progress, lifecycle stops, decisions, and a
reported model mismatch without exposing the delegated session ID.

Restart uses the same unconsumed boundary ledger. A crash before decision
persistence may request that decision again against the same packet; a recorded
decision is never duplicated, and an advance decision is consumed at most once.

## Notifications

Only newly materialized HumanTasks may use the existing typed HumanTask
notification boundary. Permissions, errors, completion, phase boundaries,
polling, empty yields, and transport activity do not gain notification
authority; they remain durable inspection outcomes.

When delivery is unavailable, CAFE does not promise to wake or update the
initiating conversation. Durable workflow truth remains available through
`cafe status` and `cafe show`; inspect it after the host returns. Version 2 adds
no notification transport, destination authority, scheduler, watchdog, or
liveness repair.
