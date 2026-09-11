[ ] Confirm: All tasks in {plan_file} are marked [x]
[ ] Read the plan **Test List** (`## Test List` in {plan_file}); every new or changed test maps to a listed item (update the plan first if scope changed)
[ ] Start `release-check` only when the effective playbook explicitly declares verification or the user explicitly requests it; risk, scale, precaution, PR preparation, review/proactive review, and agent judgment are not authority
[ ] Immediately before an authorized `release-check`, compare the confirmed plan, its checklist, and its Test List with the completed code, documentation, and tests; finish and record any missing planned work before the release check begins
[ ] After an authorized `release-check` passes, treat it as the final tracked-file validation for that exact tracked state: do not modify tracked files; only write the verification receipt, development summary, checklist status, and workflow handoff
[ ] If tracked files change after `release-check`, record why the previous release-check result is stale in the development summary, repeat this completion check, and rerun `release-check`
[ ] When `release-check` already runs the same repository-wide test command, do not run its repository-wide test command separately unless the confirmed plan explains why both runs are needed
