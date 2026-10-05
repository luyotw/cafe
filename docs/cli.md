# Compact kickoff and delivery

Use `streamlined` for an already-clear change that needs one independent native
review and an exact delivery endpoint. It declares development and delivery
readiness; it does not add specification, planning or QA steps. Existing
playbooks default to full contracts. Another playbook can opt into
`contract: {mode: compact}` while keeping its own graph and gates.

The first complete proposal contains three decision groups:

1. **Files:** exact repository-relative paths, including tests, deletions and both
   rename endpoints. For example, `src/example.py` and `tests/test_example.py`.
2. **Execution:** selected CLI/model chains and the effective native review
   configuration. The streamlined adapter supports a Claude Code custom reviewer
   with `Read`, `Glob` and `Grep`, using the confirmed inherited or overridden
   model. An unsupported provider/configuration is a focused readiness gap.
3. **Delivery:** PR source/target branches and remote, or commit/push to the exact
   designated remote branch. Direct delivery includes literal commit/push argv;
   normal hooks run and no force push or cleanup is granted.

The proposal preserves the required approval wording:

> 沒有我的同意禁止修改上面列出來的檔案

Confirmation must come from the user before implementation. A subset of the
approved files is allowed. A new path requires the existing focused HumanTask
and digest-checked replacement; the original baseline remains unchanged.
Preferences cannot rewrite confirmed authority on resume.

The Manager selects the contract form before full-only preparation. Use its
staged `prepare_kickoff.py draft/discover/assemble/render` helpers and the
[compact preparation reference](../src/cafe/data/skills/use-cafe-workflow/references/compact_kickoff.md).
Missing selected model evidence, provider capability or decisions remain explicit
gaps. A complete rendered proposal is preparation evidence, not activation.

Scope checks include committed history and current staged, unstaged and untracked
content. The system checks before every native review, before resumed work and
immediately before delivery. An unapproved committed change stays detectable even
if a later commit restores it. Failed checks report offending paths through the
existing handoff. They never expand approval or discard unrelated user changes.

Streamlined requires exactly one independent read-only native reviewer per round,
including no-change reasoning. The provider-observed invocation must follow its
checkpoint and return explicit terminal findings and targeted-test evidence.
Blocking findings require correction and fresh review within the declared attempt
budget. Content changes invalidate earlier approval; the parent cannot downgrade
an independent blocking conclusion.

Delivery readiness ends the declared worker flow. After quiescence the Manager
uses `deliver_compact.py` for PR publication through `cafe.pr.publish`, or the
existing `execute_closeout.py` for the confirmed direct commands. An unchanged
initial delivery decision is reused. Host-required approvals still create the
existing exact-request HumanTask. Report delivery only after checking actual PR
source/target/SHA or remote branch/SHA. A successful commit followed by failed push
is partial completion. Unknown external outcomes require read-only reconciliation;
attempted commands are never replayed.

Provider configuration is grounded in the installed CLI version and its command
projection. Claude's [custom subagent documentation](https://code.claude.com/docs/en/sub-agents)
describes tool restrictions and model inheritance. Codex's
[subagent documentation](https://learn.chatgpt.com/docs/agent-configuration/subagents)
describes sandbox inheritance; the current adapter therefore rejects unsupported
independent read-only review rather than claiming a stronger boundary.
