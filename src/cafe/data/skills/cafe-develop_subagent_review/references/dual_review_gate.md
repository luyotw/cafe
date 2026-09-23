## Dual-subagent review gate

[ ] Launch exactly two native subagents, `detail` and `scope`, concurrently with the same authoritative request, current diff/HEAD, and test evidence; reviewers remain read-only
[ ] Resolve every blocking finding with evidence, rerun targeted checks, and rerun both reviewers concurrently after each correction
[ ] Confirm: One current review round reports no blocking issues from both `detail` and `scope`, including when the result is no repository changes
