"""測試 cafe init 指令"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from cafe.ui.cli import app

runner = CliRunner()


class TestInitCommandEnvironmentChecks:
    """測試 init 指令的環境檢查"""

    def test_init_exits_if_config_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """測試當 .cafe/config.yaml 存在時提示並退出"""
        # 建立測試環境
        cafe_dir = tmp_path / ".cafe"
        cafe_dir.mkdir()
        config_file = cafe_dir / "config.yaml"
        config_file.write_text("test config")

        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["init"])

        assert result.exit_code == 1
        assert "設定已存在" in result.stdout
        assert "cafe config" in result.stdout

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
        assert "未找到任何支援的 AI 代理" in result.stdout

    @patch("cafe.ui.cli.shutil.which")
    @patch("cafe.ui.cli.init_helpers.copy_data_directory")
    def test_init_copies_agents_and_templates(
        self,
        mock_copy: MagicMock,
        mock_which: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """測試複製 agents 和 templates 目錄"""
        # 模擬有可用的 CLI
        mock_which.return_value = "/usr/bin/claude"

        monkeypatch.chdir(tmp_path)

        # Mock inquirer to avoid actual interaction
        with patch("cafe.ui.cli.inquirer") as mock_inquirer:
            # 模擬用戶選擇
            mock_inquirer.list_input.return_value = "claude"
            mock_inquirer.text.return_value = ""

            # Mock list_available_agents to return test data
            with patch("cafe.ui.cli.list_available_agents") as mock_list_agents:
                mock_list_agents.return_value = [
                    ("Roger", "PM agent", Path(".cafe/agents/pm/Roger.md"))
                ]

                _result = runner.invoke(app, ["init"])

        # 驗證 copy_data_directory 被呼叫兩次（agents 和 templates）
        assert mock_copy.call_count == 2

    @patch("cafe.ui.cli.shutil.which")
    @patch("cafe.ui.cli.init_helpers.copy_data_directory")
    def test_init_handles_copy_errors(
        self,
        mock_copy: MagicMock,
        mock_which: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """測試複製失敗時顯示錯誤並退出"""
        # 模擬有可用的 CLI
        mock_which.return_value = "/usr/bin/claude"

        # 模擬複製失敗
        mock_copy.side_effect = PermissionError("Permission denied")

        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["init"])

        assert result.exit_code == 1
        assert "Permission denied" in result.stdout or "錯誤" in result.stdout


class TestInitCommandInteractiveFlow:
    """測試 init 指令的互動式配置流程"""

    @patch("cafe.ui.cli.shutil.which")
    @patch("cafe.ui.cli.init_helpers.copy_data_directory")
    @patch("cafe.ui.cli.inquirer.prompt")
    @patch("cafe.ui.cli.list_available_agents")
    def test_init_prompts_for_all_three_roles(
        self,
        mock_list_agents: MagicMock,
        mock_prompt: MagicMock,
        mock_copy: MagicMock,
        mock_which: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """測試會為三個角色進行配置"""
        # 模擬有可用的 CLI
        mock_which.return_value = "/usr/bin/claude"

        # 模擬 agent 列表
        mock_list_agents.return_value = [("Roger", "PM agent", Path(".cafe/agents/pm/Roger.md"))]

        # 模擬用戶選擇 - inquirer.prompt() 返回字典
        mock_prompt.side_effect = [
            {"cli": "claude"},  # PM CLI
            {"model": ""},  # PM model
            {"agent": "Roger: PM agent"},  # PM agent
            {"cli": "gemini"},  # Developer CLI
            {"model": "sonnet"},  # Developer model
            {"agent": "Roger: PM agent"},  # Developer agent
            {"cli": "copilot"},  # Reviewer CLI
            {"model": ""},  # Reviewer model
            {"agent": "Roger: PM agent"},  # Reviewer agent
        ]

        monkeypatch.chdir(tmp_path)

        _result = runner.invoke(app, ["init"])

        # 驗證 inquirer.prompt 被呼叫 9 次（3 個角色 × 3: CLI + model + agent）
        assert mock_prompt.call_count == 9

    @patch("cafe.ui.cli.shutil.which")
    @patch("cafe.ui.cli.init_helpers.copy_data_directory")
    @patch("cafe.ui.cli.inquirer.prompt")
    @patch("cafe.ui.cli.list_available_agents")
    def test_init_handles_keyboard_interrupt(
        self,
        mock_list_agents: MagicMock,
        mock_prompt: MagicMock,
        mock_copy: MagicMock,
        mock_which: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """測試 Ctrl+C 中斷時顯示取消訊息"""
        # 模擬有可用的 CLI
        mock_which.return_value = "/usr/bin/claude"

        # 模擬 agent 列表
        mock_list_agents.return_value = [("Roger", "PM agent", Path(".cafe/agents/pm/Roger.md"))]

        # 模擬 Ctrl+C
        mock_prompt.side_effect = KeyboardInterrupt()

        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["init"])

        assert result.exit_code == 1
        assert "已取消" in result.stdout or "未完成" in result.stdout

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
        # 模擬有可用的 CLI
        mock_which.return_value = "/usr/bin/claude"

        # 模擬空的 agent 列表
        mock_list_agents.return_value = []

        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["init"])

        assert result.exit_code == 1
        # 應該顯示錯誤訊息


class TestInitCommandConfigSaving:
    """測試 init 指令的配置儲存"""

    @patch("cafe.ui.cli.shutil.which")
    @patch("cafe.ui.cli.init_helpers.copy_data_directory")
    @patch("cafe.ui.cli.inquirer.prompt")
    @patch("cafe.ui.cli.list_available_agents")
    def test_init_saves_config_correctly(
        self,
        mock_list_agents: MagicMock,
        mock_prompt: MagicMock,
        mock_copy: MagicMock,
        mock_which: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """測試配置正確儲存到 .cafe/config.yaml"""
        # 模擬有可用的 CLI
        mock_which.return_value = "/usr/bin/claude"

        # 模擬 agent 列表
        mock_list_agents.return_value = [
            ("Roger", "PM agent", Path(".cafe/agents/pm/Roger.md")),
            ("David", "Dev agent", Path(".cafe/agents/developer/David.md")),
            ("Richard", "Reviewer agent", Path(".cafe/agents/reviewer/Richard.md")),
        ]

        # 模擬用戶選擇 - inquirer.prompt() 返回字典
        mock_prompt.side_effect = [
            {"cli": "copilot"},  # PM CLI
            {"model": ""},  # PM model
            {"agent": "Roger: PM agent"},  # PM agent
            {"cli": "claude"},  # Developer CLI
            {"model": "sonnet"},  # Developer model
            {"agent": "David: Dev agent"},  # Developer agent
            {"cli": "gemini"},  # Reviewer CLI
            {"model": ""},  # Reviewer model
            {"agent": "Richard: Reviewer agent"},  # Reviewer agent
        ]

        monkeypatch.chdir(tmp_path)

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
        assert config["agents"]["developer"]["model"] == "sonnet"
        assert config["agents"]["reviewer"]["name"] == "Richard"
        assert config["agents"]["reviewer"]["cli"] == "gemini"

    @patch("cafe.ui.cli.shutil.which")
    @patch("cafe.ui.cli.init_helpers.copy_data_directory")
    @patch("cafe.ui.cli.inquirer.prompt")
    @patch("cafe.ui.cli.list_available_agents")
    def test_init_displays_success_message(
        self,
        mock_list_agents: MagicMock,
        mock_prompt: MagicMock,
        mock_copy: MagicMock,
        mock_which: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """測試成功後顯示配置摘要"""
        # 模擬有可用的 CLI
        mock_which.return_value = "/usr/bin/claude"

        # 模擬 agent 列表
        mock_list_agents.return_value = [("Roger", "PM agent", Path(".cafe/agents/pm/Roger.md"))]

        # 模擬用戶選擇 - 每個角色都選擇相同的設定
        mock_prompt.side_effect = [
            {"cli": "claude"},
            {"model": ""},
            {"agent": "Roger: PM agent"},
        ] * 3

        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["init"])

        assert result.exit_code == 0
        assert "設定已成功儲存" in result.stdout
        assert "PM:" in result.stdout
        assert "Developer:" in result.stdout
        assert "Reviewer:" in result.stdout
        assert "cafe prepare" in result.stdout

    @patch("cafe.ui.cli.shutil.which")
    @patch("cafe.ui.cli.init_helpers.copy_data_directory")
    @patch("cafe.ui.cli.inquirer.prompt")
    @patch("cafe.ui.cli.list_available_agents")
    def test_init_displays_model_as_default_when_empty(
        self,
        mock_list_agents: MagicMock,
        mock_prompt: MagicMock,
        mock_copy: MagicMock,
        mock_which: MagicMock,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """測試 model 為 None 時顯示為「預設」"""
        # 模擬有可用的 CLI
        mock_which.return_value = "/usr/bin/claude"

        # 模擬 agent 列表
        mock_list_agents.return_value = [("Roger", "PM agent", Path(".cafe/agents/pm/Roger.md"))]

        # 模擬用戶選擇（model 輸入為空）
        mock_prompt.side_effect = [
            {"cli": "claude"},
            {"model": ""},  # empty model
            {"agent": "Roger: PM agent"},
        ] * 3

        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["init"])

        assert result.exit_code == 0
        assert "預設" in result.stdout
