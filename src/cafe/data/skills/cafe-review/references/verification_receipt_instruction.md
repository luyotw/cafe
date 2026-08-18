[ ] Run `cafe verification check --output-file {develop_file} --require-scope full`
[ ] Confirm the check output's recorded command is the repository-defined full suite required by the plan and test policy; a receipt for a narrower or irrelevant command is a finding even when its process exited successfully
[ ] If the receipt is valid, do not rerun the same full suite or coverage command; when a concrete review risk requires it and the receipt is a direct pytest runner with no existing path target, run only `cafe verification focus --output-file {develop_file} -- <relative-test.py[::node-id]>`
[ ] If the receipt is missing, failed, stale, wrong-scope, or tied to a dirty worktree, record one precise finding and hand off to `develop`; do not create or repair develop evidence in review
