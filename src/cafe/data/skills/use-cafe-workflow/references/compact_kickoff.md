# Compact kickoff

Resolve the effective `contract_mode` through `prepare_kickoff.py discover`
before loading full kickoff or strategy references. Confirmed authority takes
precedence over current preferences and declarations. Absence of an opt-in means
full for new workflows. Selecting compact form preserves the declared graph and
mandatory gates; it does not change the graph's reviewer policy.

Use the ordinary staged draft, discover, assemble and render commands. Compact
inputs have three decision groups: exact repository-relative files, effective
phase CLI/model chains and provider-supported native review configuration, and
PR source/target or commit/push to an exact branch. Resolve only missing values
and selected evidence. Do not build a full proposal and remove its fields.

The execution decision group includes the actual `manager` mode and all related
settings. New compact proposals use the same mode resolution as full kickoff:
explicit choice, saved preference, then `event-driven`. Resolve its primary CLI
from the current Manager caller (`--manager-cli` or the Codex host identity),
never from a phase model. Explicit `attached` requires a positive poll interval;
explicit `unattended` has no mode-specific settings. Display an event-driven
primary CLI and any explicitly selected fallback models. Confirmed contracts
retain the complete recorded Manager object on resume even when defaults,
preferences, or current inputs change. Launch `run_workflow.py` with that exact
mode and callback configuration; creating a process establishes only a launch.
Startup failures retain a bounded sanitized diagnostic in the worker launch
record and dispatch the configured durable interruption callback before an
agent is ready. Worker stdout/stderr remain DEVNULL to avoid unbounded raw logs.

Present the complete proposal with this wording:

> 沒有我的同意禁止修改上面列出來的檔案

Read-only inspection can propose files. Wait for explicit user confirmation
before activation or implementation. Approval permits a subset; additions,
deletions, tests and both rename endpoints must fit the literal approved set.
Use the existing focused HumanTask and digest-checked replacement for expansion.
Keep the agreed baseline unchanged on expansion and resume.

System scope validation is required before every native review invocation, on
resume before further work and immediately before delivery. Detection blocks
progress; it neither authorizes expansion nor deletion of unrelated changes.
The streamlined graph requires one independent inspection-only native reviewer per
round and current terminal zero-blocker evidence. Corrections require a new
checkpoint and review within the declared attempt budget.

New Codex proposals explicitly default to
`read_only_enforcement: instruction_only`: development, native review and the
host continuation run without an OS sandbox. Read-only describes the reviewer's
inspection-only role, not enforced filesystem permissions. Include this setting
in the complete proposal. `sandbox` remains an explicit alternative. Already
confirmed contracts without this field retain their original native restrictions;
changing them requires a digest-checked contract replacement.

Deliver only to the confirmed endpoint after current review and final scope
validation. Reuse unchanged granted authority and retain mandatory host
decisions. Report a verified PR URL or pushed commit/branch; readiness, failed
push and uncertain publication do not establish delivery. Merge and issue
closure are outside compact delivery authority.

After user confirmation, prepare the declared workflow non-interactively in
the inspected current checkout (do not create a different worktree). Use the
selected graph's prepare inputs and capability-owned settings. For a graph
that declares `cafe.pr.publish`, PR delivery supplies its `--auto-create-pr`
prepare choice; mandatory host approval is still separate. Bind the rendered
`.proposal.json` to that prepared identity with
`scripts/activate_compact.py --issue-dir <issue> --proposal-file <proposal>
--confirmed-by user --confirmed-at <actual timezone-aware confirmation time>`.
Then use `run_workflow.py` with fresh contract facts. The owning projection
passes the confirmed phase chains and current scope into the generic worker.
Install those confirmed chains with `write_phase_config.py` before the first
launch; compact mode does not remove the generic phase-binding prerequisite.
For a manual-input cold start, pass the already confirmed requirements through
`run_workflow.py --initial-input '<requirements>'`. Do not substitute a manually
seeded iteration file or a direct `cafe workflow` call. The owning CLI persists
the input before spawning its worker, which consumes it at the initial-input
boundary. Once execution or a user handoff has begun, use the existing declared
continuation instead of `--initial-input`.
