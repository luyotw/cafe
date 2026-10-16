# Development delivery

## Prepared system operations

`cafe prepare --set FIELD_ID=VALUE` supplies a declared custom prepare field.
Values remain individual argv elements; they are never shell-expanded. Automatic
steps may omit agent skill/role configuration and use the `command` executor:

```yaml
commands:
  prepare:
    fields:
      - {id: export_name, type: text, label: Export name, write: export_job.name, required: true}
steps:
  export_job:
    assignee_type: auto
    automatic:
      executor: command
      inputs:
        argv: [python3, export.py, {config: export_job.name}]
        timeout: 60
    'on': {await_agent: _done}
```

Ordinary commands use the existing credential-free, network-disabled sandbox.
Sandbox failure never falls back to host execution. Each visit retains its exact
inputs and process result under the step's `systematic/` directory. A completed
command can finish its interrupted transition without running again. An unknown
or failed execution stops for recovery; it is never automatically replayed.

For a step declaring `delivery`, prepare also accepts its typed endpoint choices,
for example `--set deliver.mode=github --set deliver.target_branch=develop
--set deliver.strategy=merge --set 'deliver.verification={"not_required_reason":"An explicitly selected integration-only scope"}'`.
Local mode additionally requires `deliver.destination` and a local strategy.
The prefix is the declared step name, not a reserved phase name. All supplied
choices are validated before branch creation and take precedence over an agent's
request draft. Missing observer code does not create a release requirement;
actually required checks must remain in the reviewed verification scope.

`cafe delivery run --issue NAME --step STEP` executes or reconciles the existing
exact approved bundle through registered host capabilities, without a model.
It includes the bundle's explicitly selected follow-ups and verification, not
just its merge. Source/target SHA and PR identity are bound to current evidence
at action review, rather than guessed at preparation. Action review and host
approval remain required. This entry takes the existing advancement/workspace
locks; it grants no cleanup authority. The agent-based delivery path remains
compatible while the receipt formatter and automatic phase adoption are migrated.

## Delivery authority and evidence

The twelve adopting development playbooks separate PR preparation from delivery. Their `delivery` declarations bind the action-review step/task, action and outcome artifacts, original Review proposal artifact and implementation correction route. Names are not runtime discriminators. Compact delivery remains a separate contract.

All twelve adopting development playbooks make final delivery-result
confirmation assignable at kickoff. New Manager proposals default to delegating
that confirmation to Manager; the user may retain it instead. Manager must
verify the current complete result and receipts before answering. The separate
`need_permission` action review remains user-owned: delegating result confirmation
does not authorize integration, follow-up issue creation, or an unconfirmed
cleanup action. Existing confirmed contracts retain their recorded ownership
and require explicit reconfirmation before adopting a changed gate contract.

PR publication presents exact repository, PR or local destination, source and target commit identities, strategy, original FUP drafts, and the host capability manifests and material permission boundaries. The host freezes this bundle and the reviewed artifact digest. One correlated user decision authorizes integration and an explicit subset or empty issue selection, including only those displayed host boundaries. Delivery projects that existing decision into separate durable capability approvals with the originating task/result and snapshot identity. Registered `cafe.github.pr.merge`, `cafe.branch.integrate` and `cafe.github.issue.create` still use the existing policy revalidation and one-attempt fence; changed manifests or permissions require fresh review. Legacy proposals without a displayed host review retain their separate capability approval.

The final delivery reply accepts the verified results. New full Manager contracts store a separate `closeout_contract` with an explicit cleanup, archive, leave or pending choice and the exact ordered cleanup plan. Kickoff confirmation authorizes the displayed closeout choice; delivery acceptance never selects or changes it. After verified workflow completion and worker quiescence, Manager executes the confirmed choice without another normal confirmation. Pending choices and changed commands, targets, effects or receipts require user selection or reconfirmation. Manager proxy confirmation grants no cleanup authority. Execution retains durable non-replayable evidence.

Legacy combined tasks retain their original displayed plan and result schema. A correlated recorded user submission can supply its explicit terminal choice; a proxy or untyped historical completion cannot. Missing terminal authority is resolved separately while preserving successful integration, pending task schemas and receipts. Adopting the split contract requires user reconfirmation and normal task supersession.

Each effect records an unknown attempt atomically before dispatch. Operations have an aggregate deadline and bounded process-group cleanup, retain child exit status and require positive Git/GitHub observation. Mutation exit/timeout evidence is persisted before observation and retained across reconciliation, separately from the observed result. A zero exit, queued merge or empty search cannot establish completion. Local integration requires an explicitly approved clean destination under the existing workspace lock. No reset, stash, generic argv or conflict resolution is supplied.

Durable receipts retain actual integration commit and selected issue URLs. Stable workflow/repository/draft markers reconcile issues after interrupted responses or revised approvals. An earlier unknown identical action never gains replay permission. Partial, unknown, denied and pending results remain recoverable and nonterminal. Final acceptance requires the current completed receipt digest. Changed artifacts, source identity or selection require fresh review. Implementation corrections use the declared Develop Todo handoff.

A failed local attempt can be `settled` only after a positively recorded nonzero, non-timeout child exit and observation of the exact pre-dispatch target HEAD/tree, a clean checkout and no pending Git operation. A conflict remains unknown until independently aborted or resolved; the adapter performs no abort or repair. Zero exit without integration, timeout, dirty state or changed target cannot establish settlement. Historical settlements remain visible as failures without effects, allowing a freshly reviewed and exactly authorized correction to proceed; the original settled snapshot cannot replay its operation.

Issue creation preserves the returned number and URL before a separate direct verification. Known identities are checked against their exact repository, title and frozen marker-bearing draft. Unknown attempts use at most ten listing requests per invocation, with a durable creation-order cursor, retained matches and a page anchor that restarts observation when pagination shifts. All observed matches must be unambiguous; incomplete or negative observation of an unknown attempt never permits recreation. Read-only preflight completes before the one-shot mutating capability attempt, so repository history size cannot consume approval or impose a total-page limit.

Delivery procedures and response/outcome evidence validation belong to `cafe.delivery`; existing core hook names retain compatibility wiring. The HumanTask entry delegates those domain rules and handles its existing policy, reply and rejection routing. No general validator registry or universal completion framework is introduced.

New full Manager contracts use nested delivery schema version 6 with phase-owned delivery facts and a separate Manager closeout contract. Schema versions 3 and 5 retain their recorded legacy contracts. Kickoff schemas, templates, saved preference offers, formatter and progress do not expose Manager deliver actions. Descriptive discovery remains useful. Old delivery conventions/templates are inspectable diagnostics and cannot restore executable fields. Recorded legacy contracts retain original bytes, digests, command outcomes and narrow exact bindings; they are never silently rewritten. Cleanup, archive, branch/worktree deletion, issue closure, deployment and releases remain separate authority.

Validation uses real temporary Git repositories and an isolated fake GitHub executable at the process boundary, plus public HumanTask and capability approval callers. No test merges the development repository or creates remote issues.

Lifecycle cleanup uses neutral Git/GitHub integration evidence, independently of
Manager contracts. Delivery performs integration; `cafe close` only verifies it,
archives evidence before destructive cleanup, and removes the exact source
resources. Local target synchronization is ff-only. Fresh Manager plans use
`cafe close` as their final cleanup command; archive-only and legacy helper plans
retain their explicitly confirmed scope. Cost closeout checks the archive and
actual branch/worktree/remote absence rather than treating archival as cleanup.

Compact kickoff displays Manager mode and related settings in its execution
group. Fresh defaults are event-driven with the current Manager CLI; explicit
attached/unattended choices and saved preferences remain available. Resume
preserves confirmed mode/settings. Background startup failures before runtime
or agent construction retain one sanitized bounded diagnostic in the worker
launch record and dispatch the configured interruption callback. Process creation
is launch evidence only. DEVNULL output remains intentional; no raw unbounded
startup log is introduced.

Local delivery's existing target checkout remains in place during cleanup,
including when the source is a normal current checkout. Close preserves that
source checkout and its files by detaching the proven source commit before
compare-and-delete of its branch. Authenticated worker startup diagnostics also
cover missing, corrupt and rejected execution-context files before phase setup;
the record is failed before its durable interruption callback is dispatched.

Historical schema-3 delivery contracts containing the former close squash/message
argv remain readable with their exact bytes, digests and command arrays. The
historical read boundary is explicit; publication-mode defaults cannot enable
removed options. New activation and user-reconfirmed CAS candidates apply current
cleanup policy. A user may replace an old contract through the ordinary exact
predecessor CAS, while the public close CLI continues to reject old integration
options before touching source resources. Reading history never executes it or
silently rewrites its authority.

Bundled development delivery now declares `assignee_type: auto` with native
`automatic.executor: delivery`. Its skill supplies HumanTask policy metadata;
no agent executes its prose. `cafe prepare` requires explicit delivery settings
through the `--set <step>.<field>=...` parameters above. Action review and host
capability approvals still precede effects. The host validates receipts, writes
localized result facts and requests the existing final acceptance. Pending
verification retains the same automatic visit through `StepWaiting`.
Implementation corrections and recovery feedback route directly to the declared
correction step; `review_only` presents a new review without integrating.
Cleanup remains separate. Project catalogs retaining agent delivery hooks continue
to use their declared behavior.
