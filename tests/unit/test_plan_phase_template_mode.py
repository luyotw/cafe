"""Tests for PlanPhase template mode feature."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from cafe.phases.plan_phase import PlanPhase
from cafe.agents.manager import AgentManager
from cafe.core.git import GitOperations
from cafe.core.types import TokenUsage
from cafe.core.permission import PermissionHandler


@pytest.fixture
def mock_git_ops() -> MagicMock:
    """Create a mock GitOperations for testing."""
    git_ops = MagicMock(spec=GitOperations)
    git_ops.get_current_branch.return_value = "test-issue"
    return git_ops


def setup_agent_manager_mocks(agent_manager: MagicMock) -> None:
    """Setup standard mocks for agent_manager used by PlanPhase."""
    mock_agent = MagicMock()
    mock_agent.config.cli.value = "copilot"
    mock_agent.config.session_id = "test_session"
    agent_manager.get_agent.return_value = mock_agent
    agent_manager.get_agent_config.return_value = MagicMock(cli=MagicMock(value="copilot"))
    agent_manager.get_total_token_usage.return_value = TokenUsage()


class TestTemplateMode:
    """Test template mode feature in PlanPhase."""

    def test_init_with_auto_mode(self, mock_git_ops) -> None:
        """Test initialization with auto template mode."""
        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mocks(agent_manager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file="requirements.md",
            git_ops=mock_git_ops,
            template_mode="auto",
        )

        assert phase.template_mode == "auto"
        assert phase.template_path is None

    def test_init_with_manual_mode(self, mock_git_ops) -> None:
        """Test initialization with manual template mode."""
        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mocks(agent_manager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file="requirements.md",
            git_ops=mock_git_ops,
            template_mode="manual",
            template_path="/path/to/template.md",
        )

        assert phase.template_mode == "manual"
        assert phase.template_path == "/path/to/template.md"

    def test_checklist_includes_auto_template_instruction(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        """Test that auto mode checklist includes template selection instruction."""
        monkeypatch.chdir(tmp_path)

        from cafe.utils.checklist_generator import generate_plan_checklist

        checklist_path = tmp_path / "checklist.md"

        generate_plan_checklist(
            agent_name="Nick",
            plan_file_path=".cafe/issues/test/plan/iteration_001/output.md",
            spec_file_path=".cafe/issues/test/spec/iteration_001/output.md",
            checklist_file_path=checklist_path,
            template_mode="auto",
        )

        content = checklist_path.read_text()

        # Verify auto mode checklist includes template selection instruction
        assert "Pick a most suitable plan template" in content

    def test_checklist_includes_manual_template_instruction(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        """Test that manual mode checklist includes specific template instruction."""
        monkeypatch.chdir(tmp_path)

        from cafe.utils.checklist_generator import generate_plan_checklist

        # Create template file
        template_file = tmp_path / ".cafe" / "templates" / "plan" / "custom.md"
        template_file.parent.mkdir(parents=True, exist_ok=True)
        template_file.write_text("# Custom Template")

        checklist_path = tmp_path / "checklist.md"

        generate_plan_checklist(
            agent_name="Nick",
            plan_file_path=".cafe/issues/test/plan/iteration_001/output.md",
            spec_file_path=".cafe/issues/test/spec/iteration_001/output.md",
            checklist_file_path=checklist_path,
            template_file=".cafe/templates/plan/custom.md",
            template_mode="manual",
        )

        content = checklist_path.read_text()

        # Verify manual mode checklist includes specific template path
        assert ".cafe/templates/plan/custom.md" in content
        assert "Follow template structure" in content

    def test_template_mode_default_is_auto(self, mock_git_ops) -> None:
        """Test that default template mode is 'auto'."""
        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mocks(agent_manager)
        permission_handler = MagicMock(spec=PermissionHandler)

        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file="requirements.md",
            git_ops=mock_git_ops,
        )

        assert phase.template_mode == "auto"

    def test_load_config_with_auto_template(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        """Test that auto template from issue.yaml is loaded correctly without prompting."""
        monkeypatch.chdir(tmp_path)
        mock_git_ops.get_current_branch.return_value = "test-feature"

        # Create issue.yaml with auto template setting
        issue_dir = tmp_path / ".cafe" / "issues" / "test-feature"
        issue_dir.mkdir(parents=True, exist_ok=True)
        issue_yaml = issue_dir / "issue.yaml"
        issue_yaml.write_text("plan:\n  template: auto\n")

        # Create spec file
        spec_file = issue_dir / "spec" / "spec_001.md"
        spec_file.parent.mkdir(parents=True, exist_ok=True)
        spec_file.write_text("# Requirements")

        agent_manager = MagicMock(spec=AgentManager)
        setup_agent_manager_mocks(agent_manager)
        permission_handler = MagicMock(spec=PermissionHandler)

        # Initialize phase without explicit template_mode
        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            spec_file=str(spec_file),
            git_ops=mock_git_ops,
            interactive=True,
        )

        # Verify that template_mode was set to 'auto' from config
        assert phase.template_mode == "auto"
        assert phase.template_path is None

    def test_load_config_with_manual_template(self, tmp_path: Path, mock_git_ops, monkeypatch) -> None:
        """Test that manual template from issue.yaml is loaded correctly."""
        monkeypatch.chdir(tmp_path)
        mock_git_ops.get_current_branch.return_value = "test-feature"

        # Mock Path.home() to return tmp_path so global templates go to tmp
        from unittest.mock import patch
        with patch("cafe.utils.config.Path.home", return_value=tmp_path):
            # Create template file in global directory
            template_dir = tmp_path / ".cafe" / "templates" / "plan"
            template_dir.mkdir(parents=True, exist_ok=True)
            template_file = template_dir / "custom.md"
            template_file.write_text("# Custom Template")

            # Create issue.yaml with manual template setting
            issue_dir = tmp_path / ".cafe" / "issues" / "test-feature"
            issue_dir.mkdir(parents=True, exist_ok=True)
            issue_yaml = issue_dir / "issue.yaml"
            issue_yaml.write_text("plan:\n  template: custom\n")

            # Create spec file
            spec_file = issue_dir / "spec" / "spec_001.md"
            spec_file.parent.mkdir(parents=True, exist_ok=True)
            spec_file.write_text("# Requirements")

            agent_manager = MagicMock(spec=AgentManager)
            setup_agent_manager_mocks(agent_manager)
            permission_handler = MagicMock(spec=PermissionHandler)

            # Initialize phase without explicit template_mode
            phase = PlanPhase(
                agent_manager=agent_manager,
                permission_handler=permission_handler,
                spec_file=str(spec_file),
                    git_ops=mock_git_ops,
                interactive=True,
            )

            # Verify that template_mode was set to 'manual' and path was resolved
            assert phase.template_mode == "manual"
            # Template path should point to global directory
            assert "custom.md" in str(phase.template_path)


class TestTemplatePromptPaths:
    """測試模板提示訊息中的路徑引用"""

    def test_auto_mode_checklist_uses_absolute_paths(
        self, tmp_path: Path, mock_git_ops, monkeypatch
    ) -> None:
        """測試 auto 模式 checklist 使用絕對路徑"""
        monkeypatch.chdir(tmp_path)

        from cafe.utils.checklist_generator import generate_plan_checklist

        checklist_path = tmp_path / "checklist.md"

        generate_plan_checklist(
            agent_name="Nick",
            plan_file_path=".cafe/issues/test/plan/iteration_001/output.md",
            spec_file_path=".cafe/issues/test/spec/iteration_001/output.md",
            checklist_file_path=checklist_path,
            template_mode="auto",
        )

        content = checklist_path.read_text()

        # Verify auto mode checklist includes template selection instruction
        assert "Pick a most suitable plan template" in content
        # 應該包含可用模板列表 (使用系統內建的 templates)
        assert "`default`" in content or "`simple`" in content or "`detailed`" in content
