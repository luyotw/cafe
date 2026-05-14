# v0.2 Manual Test Log (2026-05-03)

Environment: macOS (darwin 24.6.0), branch `v02`, CLI via `.venv/bin/python -m cafe.ui.cli`

## Scope

Targeted scenarios from the v0.2 backlog:
- `cafe make`
- workflow pause/resume
- chat/handoff prompt path
- `cafe reset`
- `cafe edit`
- PR publish/receipt gating

## Test dataset

Primary issue state used for isolated runs:
- `.cafe/issues/manual-v02-smoke`
- `.cafe/issues/manual-v02-smoke-test` (branch-coupled CLI checks)

## Results

### 1) Workflow full run (dry-run)
- Command: `workflow --issue manual-v02-smoke --dry-run --user-input "Manual smoke flow input"`
- Result: **PASS**
- Observed: spec → plan → develop → review → pr, final `Workflow completed ... next=done`.

### 2) Workflow single-step pause behavior
- Command: `workflow --issue manual-v02-smoke --dry-run --single-step --start-step spec`
- Result: **PASS**
- Observed: `Workflow paused step=spec status=ready_for_review next=plan` with handoff guidance (plain outcome token + `next` pointer; no legacy `CAFE_*` prefix).

### 3) Workflow resume after pause
- Command: `workflow --issue manual-v02-smoke --dry-run`
- Result: **PASS**
- Observed: flow continued and reached `next=done`.

### 4) Single-step pause near PR boundary
- Command: `workflow --issue manual-v02-smoke --dry-run --single-step --start-step review`
- Result: **PASS**
- Observed: `Workflow paused step=review status=confirmed next=pr` (outcome token reflects agent step result; routing uses baton / blackboard).

### 5) Edit artifact command
- Command: `EDITOR=true edit spec`
- Result: **PASS**
- Observed: edited file reported as `.cafe/issues/manual-v02-smoke-test/spec/iteration_002/output.md`.

### 6) Reset command in non-TTY environment
- Command: `reset pr -i 0`
- Result: **BLOCKED (expected in Droid non-TTY)**
- Observed: confirmation prompt requires interactive terminal; command aborts before reset.

### 7) Real `make` run + host receipt gating
- Command: `make --user-input "manual make smoke"`
- Result: **PASS (expected gated pause)**
- Observed:
  - Agent CLI/tooling check passed.
  - Workflow ran PR step and then paused with:
    - `status=MISSING_CAPABILITY_RECEIPT`
    - required receipt: `pr_synced`
  - Confirms receipt-gated publish behavior is active.

## Summary

Executed scenarios confirm:
- workflow runtime pause/resume behavior is stable in dry-run mode,
- receipt-gated PR completion is enforced in execute mode,
- `edit` path works in scripted/no-op editor mode,
- `reset` currently needs interactive TTY confirmation (not executable in Droid non-TTY sessions).
