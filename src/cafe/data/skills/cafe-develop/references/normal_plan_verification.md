[ ] Confirm: All tasks in {plan_file} are marked [x]
[ ] Read the plan **Test List** (`## Test List` in {plan_file}); every new or changed test maps to a listed item (update the plan first if scope changed)
[ ] Immediately before starting `release-check`, compare the confirmed plan, its checklist, and its Test List with the completed code, documentation, and tests; finish and record any missing planned work before the release check begins
[ ] When `release-check` is required, treat it as the final tracked-file validation: after it passes, do not modify tracked files; only write the verification receipt, development summary, checklist status, and workflow handoff
[ ] If tracked files change after `release-check`, record why the previous release-check result is stale in the development summary, repeat this completion check, and rerun `release-check`
[ ] When `release-check` already runs the same repository-wide test command, do not run its repository-wide test command separately unless the confirmed plan explains why both runs are needed
