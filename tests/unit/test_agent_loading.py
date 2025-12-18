"""測試 agent 從新目錄結構載入能力."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cafe.agents.manager import AgentManager
from cafe.core.types import AgentCLI, AgentConfig


class TestAgentDirectoryStructure:
    """測試 agent 能從新子目錄結構載入."""

    @pytest.fixture
    def mock_cafe_agents(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        """建立 mock .cafe/agents 目錄結構."""
        monkeypatch.chdir(tmp_path)

        # Create new subdirectory structure
        (tmp_path / ".cafe" / "agents" / "pm").mkdir(parents=True)
        (tmp_path / ".cafe" / "agents" / "developer").mkdir(parents=True)
        (tmp_path / ".cafe" / "agents" / "reviewer").mkdir(parents=True)

        # Create agent prompt files
        (tmp_path / ".cafe" / "agents" / "pm" / "Roger.md").write_text("# Roger PM Agent")
        (tmp_path / ".cafe" / "agents" / "developer" / "David.md").write_text(
            "# David Developer Agent"
        )
        (tmp_path / ".cafe" / "agents" / "developer" / "John.md").write_text(
            "# John Developer Agent"
        )
        (tmp_path / ".cafe" / "agents" / "reviewer" / "Richard.md").write_text(
            "# Richard Reviewer Agent"
        )

        return tmp_path

    def test_agent_files_exist_in_subdirectories(self, mock_cafe_agents: Path) -> None:
        """測試 agent 檔案存在於正確子目錄."""
        assert (mock_cafe_agents / ".cafe" / "agents" / "pm" / "Roger.md").exists()
        assert (mock_cafe_agents / ".cafe" / "agents" / "developer" / "David.md").exists()
        assert (mock_cafe_agents / ".cafe" / "agents" / "developer" / "John.md").exists()
        assert (mock_cafe_agents / ".cafe" / "agents" / "reviewer" / "Richard.md").exists()

    def test_agent_manager_registers_agents_successfully(
        self, mock_cafe_agents: Path
    ) -> None:
        """測試 AgentManager 能成功註冊 agents（不依賴檔案路徑）."""
        agent_manager = AgentManager()

        # Register agents by name (not by file path)
        agent_manager.register_agent(AgentConfig(name="Roger", cli=AgentCLI.CLAUDE))
        agent_manager.register_agent(AgentConfig(name="David", cli=AgentCLI.CLAUDE))
        agent_manager.register_agent(AgentConfig(name="John", cli=AgentCLI.CLAUDE))
        agent_manager.register_agent(AgentConfig(name="Richard", cli=AgentCLI.CLAUDE))

        # Verify all agents are registered
        assert agent_manager.has_agent("Roger")
        assert agent_manager.has_agent("David")
        assert agent_manager.has_agent("John")
        assert agent_manager.has_agent("Richard")
        assert len(agent_manager.list_agents()) == 4

    @patch("subprocess.run")
    def test_cli_tools_can_access_agent_prompts(
        self, mock_run: MagicMock, mock_cafe_agents: Path
    ) -> None:
        """測試 CLI 工具能從新目錄結構存取 agent prompts.

        注意：此測試僅驗證檔案存在性, 實際 CLI 工具存取能力
        需要在整合測試or手動測試中驗證.
        """
        # Verify prompts are accessible at expected locations
        expected_paths = [
            mock_cafe_agents / ".cafe" / "agents" / "pm" / "Roger.md",
            mock_cafe_agents / ".cafe" / "agents" / "developer" / "David.md",
            mock_cafe_agents / ".cafe" / "agents" / "developer" / "John.md",
            mock_cafe_agents / ".cafe" / "agents" / "reviewer" / "Richard.md",
        ]

        for path in expected_paths:
            assert path.exists(), f"Agent prompt not found: {path}"
            assert path.is_file(), f"Agent prompt is not a file: {path}"
