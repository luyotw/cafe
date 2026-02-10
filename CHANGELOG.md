# Changelog

All notable changes to this project will be documented in this file.

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

[0.1.1]: https://github.com/luyotw/cafe/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/luyotw/cafe/releases/tag/v0.1.0
