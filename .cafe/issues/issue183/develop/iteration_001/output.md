## Changes

- 新增 `cafe skill import <path>` 指令，支援多個 skill folder 匯入、逐項衝突覆寫確認，以及 imported/skipped/failed 結果輸出。
- 新增 `src/cafe/skills/importer.py`，集中處理 skill 掃描、複製、覆寫與結果彙整，並維持部分成功時的隔離行為。
- 新增 Claude 專用 workspace 準備邏輯，agent 執行前會把專案 `.cafe/skills` 暴露到 `.claude/skills`。

## Verification

- `pytest tests/unit/test_claude_cli.py tests/unit/test_cli_catalog_commands.py tests/unit/test_skill_loader.py tests/unit/test_agent_executor.py tests/unit/test_agent_executor_path_formats.py tests/unit/test_phase_skill_bridge.py tests/unit/test_playbook_loader.py tests/unit/test_cli_show.py`
