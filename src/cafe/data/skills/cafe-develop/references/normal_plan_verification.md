[ ] Confirm: All tasks in {plan_file} are marked [x]
[ ] Read the plan **Test List** (`## Test List` in {plan_file}); every new or changed test maps to a listed item (update the plan first if scope changed)
[ ] CAFE workflow phase agents and Driver must never execute `release-check`; an in-workflow request is not executed and must be deferred until outside the active workflow, where the user may run it before release
