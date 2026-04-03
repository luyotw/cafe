# Changelog

All notable changes to this project will be documented in this file.

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

[0.1.5]: https://github.com/luyotw/cafe/compare/v0.1.4...v0.1.5
[0.1.3]: https://github.com/luyotw/cafe/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/luyotw/cafe/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/luyotw/cafe/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/luyotw/cafe/releases/tag/v0.1.0
