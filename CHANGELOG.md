# Changelog

All notable changes to this project will be documented in this file.

## [0.3.0] - 2026-08-11

This release opens the v0.3 development cycle. Roadmap capabilities assigned
to v0.3 are delivered throughout `0.3.x`; the v0.3 completion criteria are the
exit criteria for `0.4.0`.

### Breaking changes

- Removed the legacy `cafe spec`, `plan`, `develop`, `review`, and `pr`
  commands. Use `cafe make` or `cafe workflow --start-step <step> --execute`.
- Made workflow baton files strict JSON contracts. Plain step names,
  `key=value` batons, and legacy baton aliases are rejected.
- Namespaced bundled workflow skills with the `cafe-` prefix and moved workflow
  behavior to declarative skill and playbook contracts.

See [the v0.3.0 migration guide](docs/releases/v0.3.0.md) before upgrading an
installation with unfinished v0.2.x workflows or custom playbooks.

### Added

- Declarative prepare fields, human tasks, phase configuration, alignment
  checkpoints, and non-software playbooks.
- Fresh correction sessions with deterministic delta and context packets.
- Persistent long-running operation state, risk-driven monitoring, and
  `cafe operation status`.
- Verification receipts shared between develop and review.
- Automatic transactional synchronization of bundled global workflow skills.
- Codex cached-input, cache-write, reasoning-token, and provider-reported cost
  telemetry.
- A release gate that verifies coverage, contracts, package contents, and a
  clean wheel installation.

### Changed

- Renamed the workflow summary command to `cafe status`.
- Made CLI fallback, correction routing, and targeted human-task revisions
  explicit and declarative.
- Reduced repeated document loading and correction-review work while preserving
  root-cause and sibling-path review coverage.

### Fixed

- Corrected worktree configuration, agent permission, fallback classification,
  session continuation, PR publish, and user-handoff edge cases.
- Declared `click` as a direct runtime dependency so a clean wheel installation
  can start the CLI.

### Known issues

- Worktree-mode `cafe restore` remains covered by an expected-failure test while
  its project-path resolution is pending correction.

## [0.2.1]

### Added
- Added playbook-owned `commands.prepare` metadata for `cafe prepare` prompts, quick-setup defaults, non-interactive defaults, input-method behavior, and rigor constraints.
- Wired `cafe prepare` to consume the selected playbook's prepare metadata through a `PrepareProfile` decision layer while preserving default workflow behavior.
- Added the `tdd` built-in playbook and dynamic playbook choices.
- Added plan architecture sections, Dependency ADR enforcement, Test List gates, and test-invariants guidance across plan/develop/review workflow skills.

### Changed
- Moved spec and plan templates into their owning skill assets.
- Merged `spec_first` and `spec_revise` into one iteration-aware `spec` skill.
- Strengthened spec scope guarding through strategic context and anti-over-engineering review guidance.

### Fixed
- Hardened baton retry handoff contract handling.
- Ensured prepare custom-configuration rigor choices respect playbook constraints.
- Tightened prepare metadata tests so boolean validation targets the intended schema field.
- Flag unverified speculative review comments during host review.

## [0.2.0]

Major milestone release: deep refactor of CAFE's workflow engine from hardcoded phase chains to a playbook-driven, baton-first runtime with a generic blackboard state. 438 commits since v0.1.6.

### Breaking
- Removed hidden legacy phase commands `cafe spec`, `cafe plan`, `cafe develop`, `cafe review`, and `cafe pr`. Use `cafe make` or `cafe workflow --start-step <step> --execute` instead.
- Playbooks and phase hooks must use the six shipped **intent** keys (`await_agent`, `confirm_output`, `need_clarification`, `need_permission`, `manual_handoff`, `workflow_complete`) and outcome tokens without the legacy `CAFE_*` prefix. Step allow-lists use `valid_intents`; script hooks use `when_intents`. Agent prompts, bundled skill references, and checklists that still instruct models to print `CAFE_*` lines must be updated or routing and hook gates will misfire.
- Issues created before the issue241 series no longer guarantee resume compatibility. The legacy `context.json` fallbacks for `_load_user_input` and `_get_previous_iteration_status` have been removed in favor of `user_input.md` and the blackboard `step_completed` event log respectively. If you have an in-progress issue from a prior version that has not yet recorded `step_completed` events on its blackboard (or stored `user_input` only inside `context.json`), the next iteration may skip the clarification / review prompt or resume with an empty user input. Workarounds: (a) finish the issue on the previous version before upgrading, (b) re-run the issue from scratch, or (c) manually create the missing `iteration_NNN/user_input.md` file.

### Added

**Workflow Engine**
- Playbook-driven workflows with config-selectable playbooks (built-in `default`, `hotfix`, `simple`, `editorial`, `research`, `incident`) and schema validation
- `playbook simulate` command with static graph diagnostics
- `BlackboardWorkflowRuntime` replaces `PlaybookRunner` with persistent artifacts and events
- Baton-first handoffs with validated contracts persisted on issue root; reject-and-retry loop with `BatonRejected`
- Lifecycle script hooks gated by `when_intents` for spec/plan/PR sync, sandbox-safe execution
- User-owned workflow handoff menu with continue/resume options; chat handoff via blackboard + baton
- `cafe workflow --user-input` entrypoint and resume user-input helper
- Smart auto-advance that answers questions and resumes originating step
- Workflow handoff and dispute limits; show executed workflow steps during runs

**Crew & Presets**
- `cafe crew` command suite: `list`, `set-primary`, `set-fallback` with `--cli/--model/--phase-model` flags
- `CliEntry` model and CLI fallback chain in `AgentConfig`
- Built-in presets (`default`, `claude-opus`, `gemini-team`) and `cafe preset list/save/apply`
- `cafe make/workflow --fallback-preset` for rate-limit / CLI-not-found switching
- `crew.yaml`: agents migrated out of `config.yaml`; `cafe init` writes `crew.yaml` with backward-compatible loading
- `cafe prepare --preset` flag

**Skills & Native CLI Bridge**
- Native skill bridge scoped to repo-local CLI dirs with discovery errors and validation
- Project skill import/remove commands with validation
- Shared workflow skills for chat handoff seeding, `sync_github` utility, PR/spec/plan skill scripts
- Skill authoring guide for CAFE skills

**CLI & UX**
- Interactive menus: manage crew, manage agents/templates, allowed directories, issue removal
- Allowed directories: workspace-confined sandbox permissions wired through workflow
- `cafe audit` command with built-in tooling consistency checks
- `cafe edit` unified command
- Print PR URL after workflow sync; show clarification before alias retries

**Multi-CLI Support**
- Codex CLI: resume retries, model-flag handling, session recovery, env recording
- Cursor agent: `--resume` flag fix
- Rate-limit detection on stdout (Claude "hit your limit") in addition to stderr

### Fixed
- Worktree pause/resume/plan integration hardening; active-issue marker; isolated skill installs per worktree
- PR base branch defaults to `develop` when remote has it; base branch injection into PR skill context
- Stale baton recovery; graceful interruption handling (Ctrl-C / agent timeout); permission-prompt pause; stale output rejection after agent failures
- Chat handoff state preservation; bootstrap baton ignored on consumption; missing `next_step.txt` skipped
- Spec/plan: prevent dirty documents; restore initial prompt on iteration 1; require review confirmation before advancing
- Generic phase test stability; golden fixtures stabilized against local agent overrides

### Changed
- Decomposed CLI command groups into `ui/commands` modules; phase + helper surfaces split
- Unified single-step flow with shared runtime loops; eliminated `set_runtime(globals())` bridge
- Replaced status-based workflow resume with baton-driven transitions
- Legacy phase commands routed through workflow aliases
- `StatusCodeParser` marked legacy; `legacy_text` call sites documented

### Removed
- `phases_legacy.py` and its CLI registration
- `PlaybookRunner` (replaced by `BlackboardWorkflowRuntime`)
- Status-code coercion fallbacks; `BlackboardState.owner`; `require_status_code` param
- Develop status helper; `_load_pr_comments_from_iteration_file`; generic step status persistence
- `GitHubPRCreator` publish hook (PR create/update moved to skill script)
- `--auto-advance` flag (replaced by baton-first runtime fallback)

## [0.1.6]

### Added
- Track per-turn Codex token usage

### Fixed
- Correct unpack count and missing `compare_content` in spec retry loop

### Changed
- Speed up pre-commit test hooks

## [0.1.5]

### Added
- Added Codex CLI support and worktree execution handling
- Added interactive menu system — launch interactive menu when `cafe` is run without arguments
- Added `cafe setup` command with back navigation for agent configuration
- Added selective role editing flow for `cafe setup`
- Added edit option to spec and plan review menus
- Added back option to CLI selection in setup
- Added chat option to review decision, Q&A summary, clarification fallback, checkbox, and no-changes-needed prompts
- Added reusable chat launcher module
- Added PR todo list completion enforcement in review phase
- Added plan todo list posting as PR comment at PR creation/update
- Added multi-select (checkbox) support to interactive Q&A for DoD questions
- Added DoD instruction to spec checklist and templates
- Added GitHub issue discussion thread inclusion in spec phase
- Added DoD in spec phase iteration 1 so it is never skipped
- Added `cafe rm` prompting for issue name when no argument, added Remove issue to menu
- Added non-fragile test guidelines to developer agents and checklists
- Added updated simple plan template with Task structure and DoD example
- Added incremental PR comment filtering via `last_seen_comment_ids`

### Fixed
- Fixed failed host commit follow-up in develop phase
- Fixed Codex CLI unit test
- Fixed typer.Exit cascade in auto mode phase chain without hiding real errors
- Fixed auto mode error suppression that silently swallowed exceptions
- Fixed default-model semantics alignment and added reset regression test
- Fixed phase overrides preservation when default model is selected in setup
- Fixed archive and restore of config.yaml for worktree issues
- Fixed snapshot comment IDs after posting todo list to include bot comment
- Fixed dynamic version number loading
- Fixed require questions.xml for CAFE_NEED_CLARIFICATION in develop phase
- Fixed cwd upward search for template/agent file lookup to work in worktrees
- Fixed support for nested issue names (e.g. `feature/chat-web-ui`) in `cafe ls`
- Fixed PR todo list posting to only trigger when all items checked
- Fixed status_code preservation when checklist validation re-extracts from stale context.json
- Fixed colored diff display restoration after returning from chat in PR phase
- Fixed error when base_branch equals feature_branch in prepare command

### Changed
- Refined review menu labels and diff redisplay
- Cleaned up menu item names, removed misaligned description padding
- Changed manage agents/templates menu to invoke edit instead of ls

## [0.1.3]

### Added
- Added `--post-todo-list` option to `cafe pr` command to post PR comment todo list as GitHub PR comment
- Added `--post-pr-todo-list` option to `cafe prepare` command to set post-PR-todo-list mode for prepare phase
- Added token usage summary display after PR comment organization completes

### Fixed
- Fixed copilot session_id detection to recognize both file-based (.jsonl) and directory-based session formats
- Fixed `_print_token_usage_summary` to gracefully handle mocked agent_manager in tests

## [0.1.2]

### Fixed
- Fixed critical bug where `duration_ms` and `duration_api_ms` were lost when `response_parser` overwrites `token_usage` in executor
- Fixed `cafe summary` not displaying model name for PR phase iterations due to `_update_iteration_history` overwriting model with None
- Fixed `_update_iteration_history` overwriting existing field values when called multiple times (e.g., PR phase calls it after agent execution, then after PR creation)
- Fixed `cafe show output` command stripping checkbox markup `[x]` due to Rich library formatting
- Fixed checklist validation retry logic saving null status_code when retry response contained interference strings
- Fixed conditional questions.xml checklist item description to be clearer about when to check vs complete
- Fixed timeline display preventing simultaneous setting of `end_time` and `elapsed_time` fields

### Changed
- Improved `_update_iteration_history` to use incremental updates - only overwrites fields when parameters are explicitly provided
- Improved executor to preserve `duration_ms` extracted from streaming when `response_parser` returns new token_usage object
- Improved checklist retry validation to verify both checklist completion and successful status_code extraction
- Improved README "Other Features" section with better categorization (Project Setup, Workflow Execution, Monitoring & Control, Issue Management, Customization)
- Clarified `cafe reset` description to note it doesn't revert git changes

## [0.1.1]

### Added
- Added XML-based interactive Q&A system for spec phase clarifications with `questions.xml` schema
- Added interactive UI with forward/backward navigation and answer review for Q&A
- Added XML validation with automatic retry and fallback mechanisms
- Added `cafe show questions` command to view questions content
- Added `sync_github` configuration support for spec and plan phases
- Added `--sync-github` and `--no-sync-github` CLI flags for spec, plan, and prepare commands
- Added setup mode selection in prepare command for easy configuration
- Added dynamic language selection based on sync setting (matches GitHub issue language or uses native language)
- Added end-to-end integration tests for spec phase interactive Q&A flow
- Added unit tests for copilot CLI response parsing (new and old formats)
- Added tests for session_id capture in context.json
- Added tests for XML questions validation and retry mechanism

### Fixed
- Fixed copilot CLI parser to support new 'Breakdown by AI model' format while maintaining backward compatibility with old 'Usage by model' format
- Fixed missing model and session_id in `context.json` for copilot CLI executions
- Fixed timeline display error "end_time and elapsed_time cannot both be set" by preventing simultaneous setting of mutually exclusive time fields
- Fixed interactive Q&A to memorize free-text answers when navigating back through questions
- Fixed language instructions to reference "Initial Requirements" section in spec instead of ambiguous "original requirements"
- Fixed prepare command to move setup mode prompt after input method selection for better UX

### Changed
- Unified language instructions across plan and PR phase checklists
- Removed conflicting hardcoded language instructions from checklist templates
- Replaced `handled_review_timestamp` with `end_time` comparison in develop phase for consistency
- Extracted `sync_github` loading logic into shared utility function
- Deduplicated timeline entry formatting code with `_format_entry` helper method
- Improved display logic to prioritize end_time over elapsed_time in timeline entries

## [0.1.0]

### Added
- Initial release of CAFE (Computer-Aided Feature Engineering)
- Core workflow: spec → plan → develop → PR phases
- Support for multiple AI CLI tools (Claude, Copilot, Cursor, Gemini)
- Git worktree-based issue isolation
- Token usage tracking and cost estimation
- Interactive phase management with checklist validation
- GitHub integration for issue and PR management

[0.3.0]: https://github.com/luyotw/cafe/compare/v0.2.1...v0.3.0
[0.2.1]: https://github.com/luyotw/cafe/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/luyotw/cafe/compare/v0.1.6...v0.2.0
[0.1.6]: https://github.com/luyotw/cafe/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/luyotw/cafe/compare/v0.1.4...v0.1.5
[0.1.3]: https://github.com/luyotw/cafe/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/luyotw/cafe/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/luyotw/cafe/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/luyotw/cafe/releases/tag/v0.1.0
