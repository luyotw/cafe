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
   configuration. Native reviewer definitions are projected per invocation for
   Claude, Gemini, Copilot and Cursor; Codex also has a native observer but requires
   a read-only parent. Unsupported permissions or model behavior are explicit
   readiness gaps, never another standalone CLI reviewer.
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

Provider configuration is grounded in native CLI loaders and completion schemas.
CAFE leases temporary definitions for the physical parent invocation and removes
them on success or failure. It does not overwrite installed agent definitions.
Gemini's temporary home preserves the original authentication and native session
storage, including resumed executions. Parent/tool IDs, checkpoint markers and
independent conclusions must match; parent text cannot establish child success.

| CLI validated locally | Native reviewer | Supported model behavior | Completion evidence |
| --- | --- | --- | --- |
| Claude | Invocation-local `--agents`, Read/Glob/Grep | Inherited or explicit override | Matching synchronous Agent tool result |
| Gemini 0.58.0 | Temporary user agent, read_file/list_directory/glob/grep_search | Inherited or explicit override | Matching native agent progress, completed with GOAL and child result |
| Copilot 1.0.83 | Temporary plugin, view/glob/grep | Inherited or explicit override | Matching native start/completion/tool result and actual model; cancellation rejected |
| Cursor 2026.10.01-e373342 | Temporary plugin with native readonly permissions | Inherited only; plugin overrides rejected | Synchronous Task success and final child assistant message |
| Codex 0.159.3 | Temporary role config and native spawn/wait observer | Inherited or explicit override, **verified read-only API-key parent only** | Parent spawn journal, completed child and verified child model/read-only turn context |

Codex 0.159.3 deliberately inherits live parent permissions after applying role
configuration. A role's `sandbox_mode` cannot make a writable parent's child
read-only; disabling shell also leaves `apply_patch` available. CAFE rejects
writable parents and unsupported permission profiles before launch, and requires
actual read-only child journal evidence before accepting a review. Unverified
ChatGPT/cloud permission requirements are rejected; the bounded local path
requires explicit `CODEX_API_KEY` execution and rejects managed requirements,
permission profiles and extra unverified command options. This limitation
means a writable Codex development parent cannot currently use streamlined's
read-only native review contract. No separate CLI session is substituted.

These versions were checked through installed native metadata loaders without
model calls; automated tests use provider-schema transport fixtures to exercise
review and delivery validation. A provider upgrade with incompatible or missing
native evidence remains blocked rather than being assumed compatible.

See the native [Claude](https://code.claude.com/docs/en/sub-agents),
[Gemini](https://geminicli.com/docs/core/subagents/),
[Copilot](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/create-custom-agents-for-cli)
and [Cursor](https://cursor.com/docs/subagents) definitions. Codex's exact
[role projection](https://github.com/openai/codex/blob/rust-v0.159.3/codex-rs/core/src/agent/role.rs)
and [child permission inheritance](https://github.com/openai/codex/blob/rust-v0.159.3/codex-rs/core/src/agent/child_config.rs)
explain its current limitation.
