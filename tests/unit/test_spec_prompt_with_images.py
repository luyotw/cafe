"""測試 spec phase 在有圖片時產生的 prompt"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from cafe.phases.spec_phase import SpecPhase


@pytest.fixture
def mock_dependencies():
    """建立 SpecPhase 所需的 mock dependencies"""
    mock_agent_manager = MagicMock()
    mock_permission_handler = MagicMock()
    mock_git_ops = MagicMock()
    mock_git_ops.get_current_branch.return_value = "test-branch"

    return {
        "agent_manager": mock_agent_manager,
        "permission_handler": mock_permission_handler,
        "git_ops": mock_git_ops,
    }


@pytest.fixture
def spec_phase(tmp_path, mock_dependencies):
    """建立測試用的 SpecPhase instance"""
    phase = SpecPhase(
        agent_manager=mock_dependencies["agent_manager"],
        permission_handler=mock_dependencies["permission_handler"],
        git_ops=mock_dependencies["git_ops"],
        pm_agent="Roger",
        interactive=False,
        issue_name="test-issue",
        user_input="test requirements",
    )

    # 設定 phase directory 和 issue directory
    phase.issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
    phase.phase_dir = phase.issue_dir / "spec"
    phase.phase_dir.mkdir(parents=True, exist_ok=True)

    # 設定 iteration
    phase.iteration = 1
    iteration_dir = phase.phase_dir / "iteration_001"
    iteration_dir.mkdir(parents=True, exist_ok=True)
    phase.spec_file = str(iteration_dir / "output.md")

    # 設定 rigor
    phase.rigor = "low"

    return phase


class TestGenerateLocalPromptWithImages:
    """測試 _generate_local_prompt 在有圖片時的行為"""

    @patch("cafe.phases.spec_phase.AgentManager.get_agent_file_path")
    def test_有圖片時加入圖片讀取指示(self, mock_get_agent, spec_phase):
        """測試當 images 目錄存在且有圖片時，prompt 包含圖片讀取指示"""
        # Setup
        mock_get_agent.return_value = "/path/to/agent.md"

        # 建立 images 目錄並加入假圖片
        images_dir = spec_phase.phase_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        (images_dir / "image_001.png").touch()

        # Execute
        prompt = spec_phase._generate_local_prompt()

        # Verify
        assert "**Images:**" in prompt
        assert "spec/images/" in prompt or "images/" in prompt
        assert "Read tool" in prompt
        assert "UI/UX requirements" in prompt or "visual context" in prompt

    @patch("cafe.phases.spec_phase.AgentManager.get_agent_file_path")
    def test_無圖片時不加入圖片指示(self, mock_get_agent, spec_phase):
        """測試當 images 目錄不存在或為空時，prompt 不包含圖片指示"""
        # Setup
        mock_get_agent.return_value = "/path/to/agent.md"

        # Execute (images 目錄不存在)
        prompt = spec_phase._generate_local_prompt()

        # Verify
        assert "**Images:**" not in prompt

    @patch("cafe.phases.spec_phase.AgentManager.get_agent_file_path")
    def test_images目錄存在但為空(self, mock_get_agent, spec_phase):
        """測試當 images 目錄存在但為空時，不加入圖片指示"""
        # Setup
        mock_get_agent.return_value = "/path/to/agent.md"

        # 建立空的 images 目錄
        images_dir = spec_phase.phase_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

        # Execute
        prompt = spec_phase._generate_local_prompt()

        # Verify
        assert "**Images:**" not in prompt

    @patch("cafe.phases.spec_phase.AgentManager.get_agent_file_path")
    def test_圖片指示出現在正確位置(self, mock_get_agent, spec_phase):
        """測試圖片指示出現在 template instruction 之後"""
        # Setup
        mock_get_agent.return_value = "/path/to/agent.md"
        spec_phase.template_path = Path("/path/to/template.md")

        # 建立 images 目錄並加入假圖片
        images_dir = spec_phase.phase_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        (images_dir / "image_001.png").touch()

        # Execute
        prompt = spec_phase._generate_local_prompt()

        # Verify - 檢查 template 指示在前，圖片指示在後
        template_pos = prompt.find("**Template Reference:**")
        images_pos = prompt.find("**Images:**")

        # 如果 template 存在，images 應該在後面
        if template_pos != -1:
            assert images_pos > template_pos
