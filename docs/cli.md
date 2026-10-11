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
   Claude, Gemini, Copilot and Cursor. Codex uses a writable development thread
   and an independent inspection-only native fork. Unsupported permissions or model
   behavior are explicit
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
| Codex 0.159.3 | One app-server with independent native fork; new proposals default to no OS sandbox | Inherited or explicit override; ChatGPT login or API key | Actual fork identity, confirmed permission mode/model, matching child turn and independent final result |

Codex uses its authenticated native app-server for the entire reviewed invocation.
After implementation, targeted checks and the scope checkpoint, the development
thread ends a turn with the projected structured review request. CAFE forks one
native thread, verifies its distinct ID and exact parent, approved model, confirmed
permission mode and never-approval policy, then starts its inspection-only review. Its
independent conclusion returns to the same parent thread for evidence and handoff.
These are internal turns in one process, not extra workflow steps or iterations.
The parent cannot edit reviewed content or request a second review in that
continuation; corrections use the declared workflow self-loop and attempt budget.

New Codex compact proposals default explicitly to
`read_only_enforcement: instruction_only`. All three native turns use
`danger-full-access`: development, independent review and parent continuation
run without an OS sandbox. `read_only: true` describes the reviewer's role
instructions; the reviewer technically retains write access. The host records
the actual parent and reviewer permissions and that sandboxing is disabled.
Content checkpoints reject changed reviewed content before delivery, but do not
prevent writes or undo external side effects.

`read_only_enforcement: sandbox` retains a workspace-write development parent
and read-only review fork. Already confirmed contracts without this field keep
that behavior; resume never silently changes their confirmed permissions. New
proposals display the effective setting before confirmation.

ChatGPT CLI login and API-key authentication both use native Codex authentication;
CAFE neither reads credential contents nor requires an API key for this path.
Existing exact Codex sessions resume without replacing their identity. Ordinary
Codex execution and callback transports retain their existing CLI paths. Read-only
chat, empty capability scope and callback-only execution cannot activate this
writable development-review transport.

Reviewer apps, plugins, MCP servers, hooks, browser/computer tools and further
delegation are disabled. Managed requirements that force these features on are
rejected before development starts. Missing native permissions, model rerouting,
changed thread settings, cancelled/failed turns and contradictory replay remain
blocked. Only the matching child turn's final answer and explicit completion can
establish review evidence; parent prose cannot. The existing delivery gate checks
that current content still matches the scope receipt and independent conclusion.

Per-turn native counters are attributed separately to development and reviewer
models. Resumed and forked history is subtracted; absent counters remain unavailable.
CAFE does not change saved Codex configuration, authentication or installed agent
roles. Older CLI versions without the required RPCs report a readiness failure.

Automated tests use native-schema fixtures over actual stdio pipes for permissions,
continuation, replay, failure, accounting and delivery validation. Native CLI
metadata and model calls verify authenticated native fork and same-parent
continuation on the installed 0.159.3 CLI. A ChatGPT-authenticated,
instruction-only smoke completed real development commands, tests, independent
native review, same-parent continuation and delivery-readiness checks in 129
seconds. That local fixture did not publish a PR or merge remote changes.
The explicit sandbox mode requires a working native OS sandbox;
instruction-only execution does not launch that sandbox.
Compatibility with future versions still requires native evidence.

See the native [Claude](https://code.claude.com/docs/en/sub-agents),
[Gemini](https://geminicli.com/docs/core/subagents/),
[Copilot](https://docs.github.com/en/copilot/how-tos/copilot-cli/customize-copilot/create-custom-agents-for-cli)
and [Cursor](https://cursor.com/docs/subagents) definitions. Codex's native
[app-server API](https://developers.openai.com/codex/app-server) supplies explicit
thread fork and turn permission overrides; ordinary
[subagent inheritance](https://developers.openai.com/codex/subagents) does not
provide that writable-parent/read-only-child separation.
