"""備份 agent 功能測試。"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from io import StringIO

from cafe.agents.executor import AgentExecutionError
from cafe.agents.manager import AgentManager
from cafe.core.types import AgentCLI, AgentConfig, AgentResponse, CriticalPhaseError, TokenUsage


class TestAgentConfigBackupFields:
    """測試 AgentConfig 的備份相關欄位。"""

    def test_backup_clis_default_is_empty_list(self) -> None:
        """測試 backup_clis 預設值為空列表。"""
        config = AgentConfig(name="David", cli=AgentCLI.CLAUDE)
        assert config.backup_clis == []

    def test_backup_clis_accepts_list_of_agent_cli(self) -> None:
        """測試 backup_clis 接受 AgentCLI 值列表。"""
        config = AgentConfig(
            name="David",
            cli=AgentCLI.CLAUDE,
            backup_clis=[AgentCLI.GEMINI, AgentCLI.COPILOT],
        )
        assert config.backup_clis == [AgentCLI.GEMINI, AgentCLI.COPILOT]

    def test_models_config_default_is_empty_dict(self) -> None:
        """測試 models_config 預設值為空字典。"""
        config = AgentConfig(name="David", cli=AgentCLI.CLAUDE)
        assert config.models_config == {}

    def test_models_config_accepts_dict_of_dicts(self) -> None:
        """測試 models_config 接受嵌套字典結構。"""
        models = {
            "claude": {"plan": "opus", "develop": "sonnet"},
            "gemini": {"plan": "gemini-3-flash-preview", "develop": "gemini-3-flash-preview"},
        }
        config = AgentConfig(name="David", cli=AgentCLI.CLAUDE, models_config=models)
        assert config.models_config["claude"]["plan"] == "opus"
        assert config.models_config["gemini"]["develop"] == "gemini-3-flash-preview"

    def test_existing_fields_still_work(self) -> None:
        """測試新增欄位不影響現有欄位。"""
        config = AgentConfig(
            name="David",
            cli=AgentCLI.CLAUDE,
            session_id="session-123",
            model="opus",
            backup_clis=[AgentCLI.GEMINI],
            models_config={"claude": {"plan": "opus"}},
        )
        assert config.name == "David"
        assert config.cli == AgentCLI.CLAUDE
        assert config.session_id == "session-123"
        assert config.model == "opus"


class TestSetupAgentsBackupConfig:
    """測試 _setup_agents 讀取備份設定。"""

    def test_setup_agents_loads_backup_clis(self, tmp_path: Path) -> None:
        """測試 _setup_agents 從設定檔讀取 backup 清單。"""
        from cafe.ui.cli import _setup_agents
        from cafe.utils.config import ConfigManager

        config_file = tmp_path / "config.yaml"
        config_manager = ConfigManager(str(config_file))

        custom_config = {
            "agents": {
                "pm": {"name": "Roger", "cli": "copilot"},
                "developer": {
                    "name": "David",
                    "cli": "claude",
                    "backup": ["gemini", "copilot"],
                },
                "reviewer": {"name": "Richard", "cli": "copilot"},
            }
        }
        config_manager.save_config(custom_config)

        agent_manager = _setup_agents(config_manager, phase_name="develop")
        david_config = agent_manager.agents["David"].config
        assert david_config.backup_clis == [AgentCLI.GEMINI, AgentCLI.COPILOT]

    def test_setup_agents_excludes_primary_cli_from_backup(self, tmp_path: Path) -> None:
        """測試 backup 清單中與主要 CLI 相同的項目會被過濾。"""
        from cafe.ui.cli import _setup_agents
        from cafe.utils.config import ConfigManager

        config_file = tmp_path / "config.yaml"
        config_manager = ConfigManager(str(config_file))

        custom_config = {
            "agents": {
                "pm": {"name": "Roger", "cli": "copilot"},
                "developer": {
                    "name": "David",
                    "cli": "claude",
                    "backup": ["claude", "gemini"],  # "claude" 與主要 CLI 相同，應被過濾
                },
                "reviewer": {"name": "Richard", "cli": "copilot"},
            }
        }
        config_manager.save_config(custom_config)

        agent_manager = _setup_agents(config_manager, phase_name="develop")
        david_config = agent_manager.agents["David"].config
        assert AgentCLI.CLAUDE not in david_config.backup_clis
        assert AgentCLI.GEMINI in david_config.backup_clis

    def test_setup_agents_loads_models_config(self, tmp_path: Path) -> None:
        """測試 _setup_agents 從設定檔讀取 models 字典。"""
        from cafe.ui.cli import _setup_agents
        from cafe.utils.config import ConfigManager

        config_file = tmp_path / "config.yaml"
        config_manager = ConfigManager(str(config_file))

        custom_config = {
            "agents": {
                "pm": {"name": "Roger", "cli": "copilot"},
                "developer": {
                    "name": "David",
                    "cli": "claude",
                    "models": {
                        "claude": {"plan": "opus", "develop": "sonnet", "pr": "haiku"},
                        "gemini": {"plan": "gemini-3-flash-preview", "develop": "gemini-3-flash-preview"},
                    },
                },
                "reviewer": {"name": "Richard", "cli": "copilot"},
            }
        }
        config_manager.save_config(custom_config)

        agent_manager = _setup_agents(config_manager, phase_name="develop")
        david_config = agent_manager.agents["David"].config
        assert david_config.models_config["claude"]["plan"] == "opus"
        assert david_config.models_config["gemini"]["develop"] == "gemini-3-flash-preview"

    def test_setup_agents_empty_backup_when_not_configured(self, tmp_path: Path) -> None:
        """測試未設定 backup 時，backup_clis 為空列表。"""
        from cafe.ui.cli import _setup_agents
        from cafe.utils.config import ConfigManager

        config_file = tmp_path / "config.yaml"
        config_manager = ConfigManager(str(config_file))

        custom_config = {
            "agents": {
                "pm": {"name": "Roger", "cli": "copilot"},
                "developer": {"name": "David", "cli": "claude"},
                "reviewer": {"name": "Richard", "cli": "copilot"},
            }
        }
        config_manager.save_config(custom_config)

        agent_manager = _setup_agents(config_manager)
        assert agent_manager.agents["David"].config.backup_clis == []
        assert agent_manager.agents["David"].config.models_config == {}


class TestAgentManagerBackupRetry:
    """測試 AgentManager 備份 agent 重試邏輯。"""

    def _make_success_response(self, text: str = "成功") -> AgentResponse:
        return AgentResponse(response=text, token_usage=TokenUsage())

    def _make_rate_limit_error(self) -> AgentExecutionError:
        return AgentExecutionError("rate limit reached", error_type="rate_limit")

    def _make_cli_not_found_error(self) -> AgentExecutionError:
        return AgentExecutionError("cli not found", error_type="cli_not_found")

    def test_primary_succeeds_no_backup_needed(self) -> None:
        """測試主要 agent 成功時不觸發備份。"""
        manager = AgentManager()
        config = AgentConfig(
            name="David",
            cli=AgentCLI.CLAUDE,
            backup_clis=[AgentCLI.GEMINI],
        )
        manager.register_agent(config)

        with patch("cafe.agents.executor.AgentExecutor.execute") as mock_exec:
            mock_exec.return_value = self._make_success_response()
            response, *_ = manager.execute("David", "test prompt")

        assert response == "成功"
        assert mock_exec.call_count == 1

    def test_rate_limit_triggers_first_backup(self) -> None:
        """測試主要 agent 遇到 rate limit 時，自動切換到第一個備份 agent。"""
        manager = AgentManager()
        config = AgentConfig(
            name="David",
            cli=AgentCLI.CLAUDE,
            backup_clis=[AgentCLI.GEMINI],
            models_config={"gemini": {"develop": "gemini-3-flash"}},
        )
        manager.register_agent(config)

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise self._make_rate_limit_error()
            return self._make_success_response("備份成功")

        with patch("cafe.agents.executor.AgentExecutor.execute", side_effect=side_effect):
            response, *_ = manager.execute("David", "test prompt", phase_name="develop")

        assert response == "備份成功"

    def test_first_backup_fails_second_succeeds(self) -> None:
        """測試第一個備份也失敗時，繼續嘗試第二個備份。"""
        manager = AgentManager()
        config = AgentConfig(
            name="David",
            cli=AgentCLI.CLAUDE,
            backup_clis=[AgentCLI.GEMINI, AgentCLI.COPILOT],
        )
        manager.register_agent(config)

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise self._make_rate_limit_error()
            return self._make_success_response("第二備份成功")

        with patch("cafe.agents.executor.AgentExecutor.execute", side_effect=side_effect):
            response, *_ = manager.execute("David", "test prompt")

        assert response == "第二備份成功"
        assert call_count == 3

    def test_all_agents_fail_raises_error(self) -> None:
        """測試所有 agent 都失敗時，拋出 AgentExecutionError。"""
        manager = AgentManager()
        config = AgentConfig(
            name="David",
            cli=AgentCLI.CLAUDE,
            backup_clis=[AgentCLI.GEMINI],
        )
        manager.register_agent(config)

        with patch("cafe.agents.executor.AgentExecutor.execute") as mock_exec:
            mock_exec.side_effect = self._make_rate_limit_error()

            with pytest.raises(AgentExecutionError) as exc_info:
                manager.execute("David", "test prompt")

        assert exc_info.value.error_type == "rate_limit"

    def test_no_backup_configured_rate_limit_raises_immediately(self) -> None:
        """測試未設定備份 agent 時，rate limit 立即拋出錯誤（保留現有行為）。"""
        manager = AgentManager()
        config = AgentConfig(name="David", cli=AgentCLI.CLAUDE)  # 無 backup_clis
        manager.register_agent(config)

        with patch("cafe.agents.executor.AgentExecutor.execute") as mock_exec:
            mock_exec.side_effect = self._make_rate_limit_error()

            with pytest.raises(AgentExecutionError) as exc_info:
                manager.execute("David", "test prompt")

        assert exc_info.value.error_type == "rate_limit"
        assert mock_exec.call_count == 1

    def test_duplicate_cli_in_backup_is_skipped(self) -> None:
        """測試 backup 清單中重複的 CLI 只嘗試一次。"""
        manager = AgentManager()
        config = AgentConfig(
            name="David",
            cli=AgentCLI.CLAUDE,
            backup_clis=[AgentCLI.GEMINI, AgentCLI.GEMINI],  # 重複 GEMINI
        )
        manager.register_agent(config)

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise self._make_rate_limit_error()
            return self._make_success_response()

        with patch("cafe.agents.executor.AgentExecutor.execute", side_effect=side_effect):
            manager.execute("David", "test prompt")

        # 主要 + 1 個 GEMINI（不重複嘗試）
        assert call_count == 2

    def test_non_rate_limit_error_not_retried(self) -> None:
        """測試非 rate limit 的錯誤不會觸發備份重試。"""
        manager = AgentManager()
        config = AgentConfig(
            name="David",
            cli=AgentCLI.CLAUDE,
            backup_clis=[AgentCLI.GEMINI],
        )
        manager.register_agent(config)

        with patch("cafe.agents.executor.AgentExecutor.execute") as mock_exec:
            mock_exec.side_effect = AgentExecutionError("permission denied", error_type="permission_denied")

            with pytest.raises(AgentExecutionError):
                manager.execute("David", "test prompt")

        # 只嘗試一次，不觸發備份
        assert mock_exec.call_count == 1

    def test_backup_uses_phase_specific_model(self) -> None:
        """測試備份 agent 使用對應階段的 model 設定。"""
        manager = AgentManager()
        config = AgentConfig(
            name="David",
            cli=AgentCLI.CLAUDE,
            backup_clis=[AgentCLI.GEMINI],
            models_config={"gemini": {"plan": "gemini-3-flash-preview"}},
        )
        manager.register_agent(config)

        created_executors = []

        original_init = __import__("cafe.agents.executor", fromlist=["AgentExecutor"]).AgentExecutor.__init__

        def capture_executor(self, config):
            created_executors.append(config)
            original_init(self, config)

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise AgentExecutionError("rate limit", error_type="rate_limit")
            return AgentResponse(response="ok", token_usage=TokenUsage())

        with patch("cafe.agents.executor.AgentExecutor.__init__", capture_executor), \
             patch("cafe.agents.executor.AgentExecutor.execute", side_effect=side_effect):
            manager.execute("David", "test prompt", phase_name="plan")

        # 第二個 executor 應該是備份 CLI（gemini）並使用正確的 model
        backup_configs = [c for c in created_executors if c.cli == AgentCLI.GEMINI]
        assert len(backup_configs) >= 1
        assert backup_configs[0].model == "gemini-3-flash-preview"

    def test_backup_model_missing_defaults_to_none(self) -> None:
        """測試備份 agent 找不到 model 設定時，model 為 None。"""
        manager = AgentManager()
        config = AgentConfig(
            name="David",
            cli=AgentCLI.CLAUDE,
            backup_clis=[AgentCLI.GEMINI],
            models_config={},  # 無任何 model 設定
        )
        manager.register_agent(config)

        created_configs = []
        original_init = __import__("cafe.agents.executor", fromlist=["AgentExecutor"]).AgentExecutor.__init__

        def capture_executor(self, config):
            created_configs.append(config)
            original_init(self, config)

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise AgentExecutionError("rate limit", error_type="rate_limit")
            return AgentResponse(response="ok", token_usage=TokenUsage())

        with patch("cafe.agents.executor.AgentExecutor.__init__", capture_executor), \
             patch("cafe.agents.executor.AgentExecutor.execute", side_effect=side_effect):
            manager.execute("David", "test prompt", phase_name="develop")

        backup_configs = [c for c in created_configs if c.cli == AgentCLI.GEMINI]
        assert len(backup_configs) >= 1
        assert backup_configs[0].model is None

    def test_cli_not_found_backup_is_skipped(self) -> None:
        """測試備份 agent 遇到 cli_not_found 時跳過，繼續嘗試下一個。"""
        manager = AgentManager()
        config = AgentConfig(
            name="David",
            cli=AgentCLI.CLAUDE,
            backup_clis=[AgentCLI.GEMINI, AgentCLI.COPILOT],
        )
        manager.register_agent(config)

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise AgentExecutionError("rate limit", error_type="rate_limit")
            if call_count == 2:
                raise AgentExecutionError("cli not found", error_type="cli_not_found")
            return AgentResponse(response="copilot 成功", token_usage=TokenUsage())

        with patch("cafe.agents.executor.AgentExecutor.execute", side_effect=side_effect):
            response, *_ = manager.execute("David", "test prompt")

        assert response == "copilot 成功"
        assert call_count == 3


class TestRateLimitErrorDisplay:
    """測試 rate limit 錯誤顯示訊息。"""

    def _make_critical_rate_limit_error(self, message: str) -> CriticalPhaseError:
        return CriticalPhaseError(message=message, error_type="rate_limit", phase_name="develop")

    def test_rate_limit_suggests_backup_config_when_no_backups(self, capsys) -> None:
        """測試未設定備份時，錯誤訊息建議設定備份 agent。"""
        import typer
        from cafe.ui.cli import _handle_phase_exception

        error = self._make_critical_rate_limit_error("rate limit reached")
        with pytest.raises(typer.Exit):
            _handle_phase_exception(error, "develop")

        captured = capsys.readouterr()
        assert "backup" in captured.out.lower() or "cafe config edit" in captured.out

    def test_rate_limit_shows_tried_agents_when_all_exhausted(self, capsys) -> None:
        """測試所有 agent 都失敗時，錯誤訊息顯示已嘗試的 agent 清單。"""
        import typer
        from cafe.ui.cli import _handle_phase_exception

        message = (
            "All agents failed. Tried: claude (rate limit reached), gemini (rate limit reached). "
            "Please wait for rate limits to reset or add more backup agents."
        )
        error = self._make_critical_rate_limit_error(message)
        with pytest.raises(typer.Exit):
            _handle_phase_exception(error, "develop")

        captured = capsys.readouterr()
        # Should show that multiple agents were tried
        assert "claude" in captured.out or "All agents failed" in captured.out
