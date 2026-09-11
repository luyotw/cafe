# Release authority decision contract

Apply this matrix before starting `release-check`. It defines the develop
phase's declared authority decision; it does not create an authorization that
the effective playbook or user has not supplied.

| Scenario | Authority input | Work/scope state | Decision |
| --- | --- | --- | --- |
| current-develop-step-assignment | Effective playbook assigns `release-check` to the current `develop` step | Initial run | allow |
| separate-verify-step-assignment | Effective playbook assigns `release-check` to a separate `verify` step | Initial run | deny |
| separate-verification-step-assignment | Effective playbook assigns `release-check` to a separate `verification` step | Initial run | deny |
| same-work-scope-user-stale-rerun | Original explicit user request remains in effect | Stale rerun for the same authorized work/scope | allow |
| authority-withdrawn | Original authority has been withdrawn | Stale rerun | reauthorize |
| material-work-scope-change | Original authority remains, but work/scope identity materially changed | Stale rerun | reauthorize |
