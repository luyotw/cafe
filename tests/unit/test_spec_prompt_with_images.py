"""Test spec phase prompt generation with images"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from cafe.phases.spec_phase import SpecPhase


@pytest.fixture
def mock_dependencies():
    """Create mock dependencies for SpecPhase"""
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
    """Create a SpecPhase instance for testing"""
    phase = SpecPhase(
        agent_manager=mock_dependencies["agent_manager"],
        permission_handler=mock_dependencies["permission_handler"],
        git_ops=mock_dependencies["git_ops"],
        pm_agent="Roger",
        interactive=False,
        issue_name="test-issue",
        user_input="test requirements",
    )

    # Set up phase directory and issue directory
    phase.issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
    phase.phase_dir = phase.issue_dir / "spec"
    phase.phase_dir.mkdir(parents=True, exist_ok=True)

    # Set up iteration
    phase.iteration = 1
    iteration_dir = phase.phase_dir / "iteration_001"
    iteration_dir.mkdir(parents=True, exist_ok=True)
    phase.spec_file = str(iteration_dir / "output.md")

    # Set up rigor
    phase.rigor = "low"

    return phase


class TestGenerateLocalPromptWithImages:
    """Test _generate_local_prompt behavior with images"""

    @patch("cafe.phases.spec_phase.AgentManager.get_agent_file_path")
    def test_includes_image_instruction_when_images_exist(self, mock_get_agent, spec_phase):
        """Test prompt includes image reading instruction when images directory exists with files"""
        # Setup
        mock_get_agent.return_value = "/path/to/agent.md"

        # Create images directory with a test image
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
    def test_no_image_instruction_when_no_images(self, mock_get_agent, spec_phase):
        """Test prompt excludes image instruction when images directory doesn't exist"""
        # Setup
        mock_get_agent.return_value = "/path/to/agent.md"

        # Execute (images directory doesn't exist)
        prompt = spec_phase._generate_local_prompt()

        # Verify
        assert "**Images:**" not in prompt

    @patch("cafe.phases.spec_phase.AgentManager.get_agent_file_path")
    def test_no_image_instruction_when_images_dir_empty(self, mock_get_agent, spec_phase):
        """Test prompt excludes image instruction when images directory exists but is empty"""
        # Setup
        mock_get_agent.return_value = "/path/to/agent.md"

        # Create empty images directory
        images_dir = spec_phase.phase_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

        # Execute
        prompt = spec_phase._generate_local_prompt()

        # Verify
        assert "**Images:**" not in prompt

    @patch("cafe.phases.spec_phase.AgentManager.get_agent_file_path")
    def test_image_instruction_position(self, mock_get_agent, spec_phase):
        """Test image instruction appears after template instruction"""
        # Setup
        mock_get_agent.return_value = "/path/to/agent.md"
        spec_phase.template_path = Path("/path/to/template.md")

        # Create images directory with a test image
        images_dir = spec_phase.phase_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        (images_dir / "image_001.png").touch()

        # Execute
        prompt = spec_phase._generate_local_prompt()

        # Verify - template instruction should come before images instruction
        template_pos = prompt.find("**Template Reference:**")
        images_pos = prompt.find("**Images:**")

        # If template exists, images should come after
        if template_pos != -1:
            assert images_pos > template_pos
