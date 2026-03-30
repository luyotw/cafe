"""測試 cafe init 指令"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from cafe.ui.cli import app, CUSTOM_MODEL_SENTINEL

runner = CliRunner()


def _default_prompt_list_side_effect(cli="claude", agent="Roger: PM agent (system default)"):
    """Build prompt_list side_effect for all-default init flow.

    New flow: each role has CLI + agent + model steps (all via prompt_list).
    PM: 3 calls (cli, agent, spec_model)
    Developer: 5 calls (cli, agent, plan_model, develop_model, pr_model)
    Reviewer: 3 calls (cli, agent, review_model)
    Total: 11 calls
    """
    return [
        cli, agent, "",              # PM
        cli, agent, "", "", "",      # Developer
        cli, agent, "",              # Reviewer
    ]


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
        mock_list_agents.return_value = [("Roger", "PM agent", Path(".cafe/roles/pm/Roger.md"), "system default")]

        monkeypatch.chdir(tmp_path)

        with (
            patch("cafe.ui.cli.prompt_confirm") as mock_confirm,
            patch("cafe.ui.cli.prompt_list") as mock_prompt_list,
            patch("cafe.ui.cli.prompt_text") as mock_prompt_text
        ):
            mock_confirm.side_effect = [True]
            mock_prompt_list.side_effect = _default_prompt_list_side_effect()

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
        mock_which.return_value = "/usr/bin/claude"
        mock_list_agents.return_value = [("Roger", "PM agent", Path(".cafe/roles/pm/Roger.md"), "system default")]
        monkeypatch.chdir(tmp_path)

        with (
            patch("cafe.ui.cli.prompt_list") as mock_prompt_list,
            patch("cafe.ui.cli.prompt_text") as mock_prompt_text,
        ):
            agent = "Roger: PM agent (system default)"
            mock_prompt_list.side_effect = [
                "claude", agent, "",                      # PM
                "gemini", agent, CUSTOM_MODEL_SENTINEL, "", "",  # Developer: plan=custom
                "copilot", agent, "",                     # Reviewer
            ]
            mock_prompt_text.side_effect = ["sonnet"]  # Developer plan model

            _result = runner.invoke(app, ["init"])

        # 驗證 prompt_list: PM(3) + Developer(5) + Reviewer(3) = 11
        assert mock_prompt_list.call_count == 11
        # 驗證 prompt_text: only 1 custom model
        assert mock_prompt_text.call_count == 1

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
        mock_which.return_value = "/usr/bin/claude"
        mock_list_agents.return_value = [("Roger", "PM agent", Path(".cafe/roles/pm/Roger.md"), "system default")]
        monkeypatch.chdir(tmp_path)

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
        mock_which.return_value = "/usr/bin/claude"
        mock_list_agents.return_value = []
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["init"])

        assert result.exit_code == 1


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
        mock_which.return_value = "/usr/bin/claude"
        mock_list_agents.return_value = [
            ("Roger", "PM agent", Path(".cafe/roles/pm/Roger.md"), "system default"),
            ("David", "Dev agent", Path(".cafe/roles/developer/David.md"), "custom"),
            ("Richard", "Reviewer agent", Path(".cafe/roles/reviewer/Richard.md"), "system default"),
        ]
        monkeypatch.chdir(tmp_path)

        with (
            patch("cafe.ui.cli.prompt_list") as mock_prompt_list,
            patch("cafe.ui.cli.prompt_text") as mock_prompt_text,
        ):
            mock_prompt_list.side_effect = [
                "copilot", "Roger: PM agent (system default)", "",              # PM: all default
                "claude", "David: Dev agent (custom)", CUSTOM_MODEL_SENTINEL, "", "",  # Dev: plan=custom
                "gemini", "Richard: Reviewer agent (system default)", "",       # Reviewer: all default
            ]
            mock_prompt_text.side_effect = ["sonnet"]  # Developer plan model

            _result = runner.invoke(app, ["init"])

        config_file = tmp_path / ".cafe" / "config.yaml"
        assert config_file.exists()

        import yaml
        with open(config_file) as f:
            config = yaml.safe_load(f)

        assert config["agents"]["pm"]["name"] == "Roger"
        assert config["agents"]["pm"]["cli"] == "copilot"
        assert config["agents"]["developer"]["name"] == "David"
        assert config["agents"]["developer"]["cli"] == "claude"
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
        mock_which.return_value = "/usr/bin/claude"
        mock_list_agents.return_value = [("Roger", "PM agent", Path(".cafe/roles/pm/Roger.md"), "system default")]
        monkeypatch.chdir(tmp_path)

        with (
            patch("cafe.ui.cli.prompt_list") as mock_prompt_list,
            patch("cafe.ui.cli.prompt_text") as mock_prompt_text,
        ):
            mock_prompt_list.side_effect = _default_prompt_list_side_effect()
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
        mock_which.return_value = "/usr/bin/claude"
        mock_list_agents.return_value = [("Roger", "PM agent", Path(".cafe/roles/pm/Roger.md"), "system default")]
        monkeypatch.chdir(tmp_path)

        with (
            patch("cafe.ui.cli.prompt_list") as mock_prompt_list,
            patch("cafe.ui.cli.prompt_text") as mock_prompt_text,
        ):
            mock_prompt_list.side_effect = _default_prompt_list_side_effect()
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
        """測試當沒有 agents 時錯誤訊息應該顯示正確的路徑"""
        mock_which.return_value = "/usr/bin/claude"
        mock_prompt_list.return_value = "claude"
        mock_prompt_text.return_value = ""
        mock_list_agents.return_value = []

        monkeypatch.chdir(tmp_path)

        result = runner.invoke(app, ["init"])

        assert result.exit_code == 1
        assert "Agent files not found" in result.stdout
        assert "in .cafe/roles/" not in result.stdout
        assert "~" in result.stdout and "/.cafe/roles/" in result.stdout
        assert "src/cafe/data/roles/" in result.stdout
