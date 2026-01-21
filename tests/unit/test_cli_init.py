"""測試 cafe init 指令"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from cafe.ui.cli import app

runner = CliRunner()


class TestInitCommandEnvironmentChecks:
    """測試 init 指令環境檢查"""

    def test_init_prompts_for_overwrite_if_config_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """測試當 .cafe/config.yaml 存在時提示是否覆寫"""
        # 建立測試環境
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        config_file = cafe_dir / "config.yaml"
        config_file.write_text("test config")

        monkeypatch.chdir(tmp_path)

        # Mock prompt_confirm to return False (user cancels)
        with patch("cafe.ui.cli.prompt_confirm") as mock_confirm:
            mock_confirm.return_value = False

            result = runner.invoke(app, ["init"])

        assert result.exit_code == 0
        assert "Configuration already exists" in result.stdout
        assert "Cancelled" in result.stdout
        mock_confirm.assert_called_once()

    @patch("cafe.ui.cli.shutil.which")
    @patch("cafe.ui.cli.init_helpers.copy_data_directory")
    @patch("cafe.ui.cli.list_available_agents")
    def test_init_overwrites_config_when_confirmed(
        self,
        mock_list_agents: MagicMock,
        mock_copy: MagicMock,
        mock_which: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """測試當使用者確認覆寫時，會重新執行 init"""
        # 建立測試環境
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        config_file = cafe_dir / "config.yaml"
        config_file.write_text("old config")

        # 模擬有可用 CLI
        mock_which.return_value = "/usr/bin/claude"

        # 模擬 agent 列表
        mock_list_agents.return_value = [("Roger", "PM agent", Path(".cafe/agents/pm/Roger.md"), "system default")]

        monkeypatch.chdir(tmp_path)

        # Mock prompts
        with (
            patch("cafe.ui.cli.prompt_confirm") as mock_confirm,
            patch("cafe.ui.cli.prompt_list") as mock_prompt_list,
            patch("cafe.ui.cli.prompt_text") as mock_prompt_text
        ):

            # User confirms overwrite (True)
            mock_confirm.side_effect = [True]

            # Setup agent selection
            mock_prompt_list.side_effect = [
                "claude",
                "Roger: PM agent (system default)",
                "claude",
                "Roger: PM agent (system default)",
                "claude",
                "Roger: PM agent (system default)",
            ]
            # 設定 prompt_text 返回值（phase-specific models only）
            # PM: spec phase model
            # Developer: plan, develop, pr phase models
            # Reviewer: review phase model
            mock_prompt_text.side_effect = [
                "",  # PM: spec
                "", "", "",  # Developer: plan, develop, pr
                ""  # Reviewer: review
            ]

            result = runner.invoke(app, ["init"])

        assert result.exit_code == 0
        assert "Proceeding to overwrite" in result.stdout
        assert "Configuration saved successfully" in result.stdout

        # Verify config was overwritten
        assert config_file.exists()
        content = config_file.read_text()
        assert "old config" not in content

    @patch("cafe.ui.cli.shutil.which")
    def test_init_exits_if_no_clis_available(
        self, mock_which: MagicMock, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """測試當無可用 CLI 時提示錯誤並退出"""
        # 模擬所有 CLI 都不存在
        mock_which.return_value = None

        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["init"])

        assert result.exit_code == 1
        assert "No supported AI agents found" in result.stdout

    # Tests removed: Agents and templates are no longer copied to project .cafe directory
    # They are now managed globally at ~/.cafe/


class TestInitCommandInteractiveFlow:
    """測試 init 指令互動式配置流程"""

    @patch("cafe.ui.cli.shutil.which")
    @patch("cafe.ui.cli.init_helpers.copy_data_directory")
    @patch("cafe.ui.cli.list_available_agents")
    def test_init_prompts_for_all_three_roles(
        self,
        mock_list_agents: MagicMock,
        mock_copy: MagicMock,
        mock_which: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """測試會為三個角色進行配置"""
        # 模擬有可用 CLI
        mock_which.return_value = "/usr/bin/claude"

        # 模擬 agent 列表
        mock_list_agents.return_value = [("Roger", "PM agent", Path(".cafe/agents/pm/Roger.md"), "system default")]

        monkeypatch.chdir(tmp_path)

        # 模擬 prompt_list, prompt_text, prompt_confirm 方法
        with (
            patch("cafe.ui.cli.prompt_list") as mock_prompt_list,
            patch("cafe.ui.cli.prompt_text") as mock_prompt_text,
            patch("cafe.ui.cli.prompt_confirm") as mock_prompt_confirm
        ):
            
            # 設定 prompt_list 返回值（CLI and agent 選擇）
            mock_prompt_list.side_effect = [
                "claude",  # PM CLI
                "Roger: PM agent (system default)",  # PM agent
                "gemini",  # Developer CLI
                "Roger: PM agent (system default)",  # Developer agent
                "copilot",  # Reviewer CLI
                "Roger: PM agent (system default)",  # Reviewer agent
            ]

            # 設定 prompt_text 返回值（phase-specific models only）
            # PM: spec phase model
            # Developer: plan, develop, pr phase models
            # Reviewer: review phase model
            mock_prompt_text.side_effect = [
                "",  # PM: spec
                "sonnet", "", "",  # Developer: plan, develop, pr
                ""  # Reviewer: review
            ]

            # 設定 prompt_confirm 返回值（不再使用）
            mock_prompt_confirm.return_value = False

            _result = runner.invoke(app, ["init"])

        # 驗證 prompt_list 被呼叫 6 次（3 個角色 × 2: CLI + agent）
        assert mock_prompt_list.call_count == 6
        # 驗證 prompt_text 被呼叫 5 次（PM: 1, Developer: 3, Reviewer: 1）
        assert mock_prompt_text.call_count == 5

    @patch("cafe.ui.cli.shutil.which")
    @patch("cafe.ui.cli.init_helpers.copy_data_directory")
    @patch("cafe.ui.cli.list_available_agents")
    def test_init_handles_keyboard_interrupt(
        self,
        mock_list_agents: MagicMock,
        mock_copy: MagicMock,
        mock_which: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """測試 Ctrl+C 中斷時顯示取消訊息"""
        # 模擬有可用 CLI
        mock_which.return_value = "/usr/bin/claude"

        # 模擬 agent 列表
        mock_list_agents.return_value = [("Roger", "PM agent", Path(".cafe/agents/pm/Roger.md"), "system default")]

        monkeypatch.chdir(tmp_path)

        # 模擬 Ctrl+C
        with patch("cafe.ui.cli.prompt_list") as mock_prompt_list:
            mock_prompt_list.side_effect = KeyboardInterrupt()

            result = runner.invoke(app, ["init"])

        assert result.exit_code == 1
        assert "cancelled" in result.stdout or "未完成" in result.stdout

    @patch("cafe.ui.cli.shutil.which")
    @patch("cafe.ui.cli.init_helpers.copy_data_directory")
    @patch("cafe.ui.cli.list_available_agents")
    def test_init_errors_on_empty_agent_directory(
        self,
        mock_list_agents: MagicMock,
        mock_copy: MagicMock,
        mock_which: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """測試空 agent 資料夾時提示錯誤"""
        # 模擬有可用 CLI
        mock_which.return_value = "/usr/bin/claude"

        # 模擬空 agent 列表
        mock_list_agents.return_value = []

        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["init"])

        assert result.exit_code == 1
        # 應該顯示錯誤訊息


class TestInitCommandConfigSaving:
    """測試 init 指令配置儲存"""

    @patch("cafe.ui.cli.shutil.which")
    @patch("cafe.ui.cli.init_helpers.copy_data_directory")
    @patch("cafe.ui.cli.list_available_agents")
    def test_init_saves_config_correctly(
        self,
        mock_list_agents: MagicMock,
        mock_copy: MagicMock,
        mock_which: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """測試配置正確儲存到 .cafe/config.yaml"""
        # 模擬有可用 CLI
        mock_which.return_value = "/usr/bin/claude"

        # 模擬 agent 列表
        mock_list_agents.return_value = [
            ("Roger", "PM agent", Path(".cafe/agents/pm/Roger.md"), "system default"),
            ("David", "Dev agent", Path(".cafe/agents/developer/David.md"), "custom"),
            ("Richard", "Reviewer agent", Path(".cafe/agents/reviewer/Richard.md"), "system default"),
        ]

        monkeypatch.chdir(tmp_path)

        # 模擬 prompt_list, prompt_text, prompt_confirm 方法
        with (
            patch("cafe.ui.cli.prompt_list") as mock_prompt_list,
            patch("cafe.ui.cli.prompt_text") as mock_prompt_text,
            patch("cafe.ui.cli.prompt_confirm") as mock_prompt_confirm
        ):
            
            # 設定 prompt_list 返回值（CLI and agent 選擇）
            mock_prompt_list.side_effect = [
                "copilot",  # PM CLI
                "Roger: PM agent (system default)",  # PM agent
                "claude",  # Developer CLI
                "David: Dev agent (custom)",  # Developer agent
                "gemini",  # Reviewer CLI
                "Richard: Reviewer agent (system default)",  # Reviewer agent
            ]

            # 設定 prompt_text 返回值（phase-specific models only）
            # PM: spec phase model
            # Developer: plan, develop, pr phase models
            # Reviewer: review phase model
            mock_prompt_text.side_effect = [
                "",  # PM: spec
                "sonnet", "", "",  # Developer: plan, develop, pr
                ""  # Reviewer: review
            ]

            # 設定 prompt_confirm 返回值（不再使用）
            mock_prompt_confirm.return_value = False

            _result = runner.invoke(app, ["init"])

        # 驗證配置檔案被建立
        config_file = tmp_path / ".cafe" / "config.yaml"
        assert config_file.exists()

        # 讀取並驗證配置內容
        import yaml

        with open(config_file) as f:
            config = yaml.safe_load(f)

        assert config["agents"]["pm"]["name"] == "Roger"
        assert config["agents"]["pm"]["cli"] == "copilot"
        assert config["agents"]["developer"]["name"] == "David"
        assert config["agents"]["developer"]["cli"] == "claude"
        # Verify phase-specific models are stored (not role-level)
        assert config["agents"]["developer"]["plan"]["model"] == "sonnet"
        assert "model" not in config["agents"]["pm"]
        assert "model" not in config["agents"]["developer"]
        assert "model" not in config["agents"]["reviewer"]
        assert config["agents"]["reviewer"]["name"] == "Richard"
        assert config["agents"]["reviewer"]["cli"] == "gemini"

    @patch("cafe.ui.cli.shutil.which")
    @patch("cafe.ui.cli.init_helpers.copy_data_directory")
    @patch("cafe.ui.cli.list_available_agents")
    def test_init_displays_success_message(
        self,
        mock_list_agents: MagicMock,
        mock_copy: MagicMock,
        mock_which: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """測試成功後顯示配置摘要"""
        # 模擬有可用 CLI
        mock_which.return_value = "/usr/bin/claude"

        # 模擬 agent 列表
        mock_list_agents.return_value = [("Roger", "PM agent", Path(".cafe/agents/pm/Roger.md"), "system default")]

        monkeypatch.chdir(tmp_path)

        # 模擬 prompt_list, prompt_text, prompt_confirm 方法
        with (
            patch("cafe.ui.cli.prompt_list") as mock_prompt_list,
            patch("cafe.ui.cli.prompt_text") as mock_prompt_text,
            patch("cafe.ui.cli.prompt_confirm") as mock_prompt_confirm
        ):
            
            # 每個角色都選擇相同設定
            mock_prompt_list.side_effect = [
                "claude",
                "Roger: PM agent (system default)",
                "claude",
                "Roger: PM agent (system default)",
                "claude",
                "Roger: PM agent (system default)",
            ]
            # 設定 prompt_text 返回值（phase-specific models only）
            # PM: spec phase model
            # Developer: plan, develop, pr phase models
            # Reviewer: review phase model
            mock_prompt_text.side_effect = [
                "",  # PM: spec
                "", "", "",  # Developer: plan, develop, pr
                ""  # Reviewer: review
            ]

            # 設定 prompt_confirm 返回值（不再使用）
            mock_prompt_confirm.return_value = False

            result = runner.invoke(app, ["init"])

        assert result.exit_code == 0
        assert "Configuration saved successfully" in result.stdout
        assert "PM:" in result.stdout
        assert "Developer:" in result.stdout
        assert "Reviewer:" in result.stdout
        assert "cafe prepare" in result.stdout

    @patch("cafe.ui.cli.shutil.which")
    @patch("cafe.ui.cli.init_helpers.copy_data_directory")
    @patch("cafe.ui.cli.list_available_agents")
    def test_init_displays_model_as_default_when_empty(
        self,
        mock_list_agents: MagicMock,
        mock_copy: MagicMock,
        mock_which: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """測試 model 為 None 時顯示為「預設」"""
        # 模擬有可用 CLI
        mock_which.return_value = "/usr/bin/claude"

        # 模擬 agent 列表
        mock_list_agents.return_value = [("Roger", "PM agent", Path(".cafe/agents/pm/Roger.md"), "system default")]

        monkeypatch.chdir(tmp_path)

        # 模擬 prompt_list, prompt_text, prompt_confirm 方法（model 輸入為空）
        with (
            patch("cafe.ui.cli.prompt_list") as mock_prompt_list,
            patch("cafe.ui.cli.prompt_text") as mock_prompt_text,
            patch("cafe.ui.cli.prompt_confirm") as mock_prompt_confirm
        ):
            
            mock_prompt_list.side_effect = [
                "claude",
                "Roger: PM agent (system default)",
                "claude",
                "Roger: PM agent (system default)",
                "claude",
                "Roger: PM agent (system default)",
            ]
            # 設定 prompt_text 返回值（phase-specific models only）
            # PM: spec phase model
            # Developer: plan, develop, pr phase models
            # Reviewer: review phase model
            mock_prompt_text.side_effect = [
                "",  # PM: spec
                "", "", "",  # Developer: plan, develop, pr
                ""  # Reviewer: review
            ]

            # 設定 prompt_confirm 返回值（不再使用）
            mock_prompt_confirm.return_value = False

            result = runner.invoke(app, ["init"])

        assert result.exit_code == 0
        assert "default" in result.stdout


class TestInitCommandErrorMessages:
    """測試 init 指令的錯誤訊息"""

    @patch("cafe.ui.cli.shutil.which")
    @patch("cafe.ui.cli.list_available_agents")
    @patch("cafe.ui.cli.prompt_list")
    @patch("cafe.ui.cli.prompt_text")
    def test_no_agents_error_message_shows_correct_paths(
        self,
        mock_prompt_text: MagicMock,
        mock_prompt_list: MagicMock,
        mock_list_agents: MagicMock,
        mock_which: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """測試當沒有 agents 時錯誤訊息應該顯示正確的路徑 (不應該顯示 .cafe/agents/)"""
        # 模擬有可用 CLI
        mock_which.return_value = "/usr/bin/claude"

        # 模擬選擇 CLI
        mock_prompt_list.return_value = "claude"
        
        # 模擬輸入 model (empty string)
        mock_prompt_text.return_value = ""
        
        # 模擬空 agent 列表
        mock_list_agents.return_value = []

        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["init"])

        assert result.exit_code == 1
        assert "Agent files not found" in result.stdout
        # 不應該包含錯誤的 .cafe/agents/ 路徑引用 (但允許 ~/.cafe/agents/)
        # 檢查是否包含正確引用而不是錯誤引用 "Please ensure valid .md files exist in .cafe/agents/"
        assert "in .cafe/agents/" not in result.stdout
        # 應該提示正確的路徑：~/.cafe/agents/ 和 src/cafe/data/agents/
        # Note: ANSI color codes may split the path, so check for the key components
        assert "~" in result.stdout and "/.cafe/agents/" in result.stdout
        assert "src/cafe/data/agents/" in result.stdout
