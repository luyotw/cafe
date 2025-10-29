#!/usr/bin/env python3
"""Integration test for spec phase with real agent execution."""

import pytest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from aaf.agents.manager import AgentManager
from aaf.core.permission import PermissionHandler
from aaf.core.types import AgentConfig, AgentCLI, WorkflowMode, PhaseStatus
from aaf.phases.spec_phase import SpecPhase


@pytest.mark.integration
def test_spec_phase_first_iteration_with_user_story():
    """Test first iteration of spec phase with mocked user input.

    This test verifies that:
    - SpecPhase can start with a user story
    - PM agent processes the story and asks clarification questions
    - spec.md and status.json are created correctly
    """
    # Setup
    agent_manager = AgentManager()
    agent_manager.register_agent(
        AgentConfig(name="Roger", cli=AgentCLI.GEMINI)
    )

    permission_handler = PermissionHandler()

    # Create spec phase
    output_file = "test_myip_integration.md"

    # Clean up previous test files
    for f in [output_file, ".aaf"]:
        path = Path(f)
        if path.exists():
            if path.is_dir():
                import shutil
                shutil.rmtree(path)
            else:
                path.unlink()

    phase = SpecPhase(
        agent_manager=agent_manager,
        permission_handler=permission_handler,
        spec_file=output_file,
        workflow_mode=WorkflowMode.LOCAL,
        interactive=True,
        pm_agent="Roger",
    )

    # Prepare user input
    user_story = "身為開發者，我想要有一個指令 `myip` 可以顯示我的內網 IP 和外網 IP，以便我可以快速複製貼上到別的地方"
    user_inputs = [user_story, "END"]

    mock_stdin = StringIO("\n".join(user_inputs) + "\n")

    # Mock stdin for display.get_multiline_input
    with patch('sys.stdin', mock_stdin):
        result = phase.execute()

        # Verify result
        assert result.status == PhaseStatus.IN_PROGRESS
        assert phase.iteration == 1

    # Check spec.md was created
    spec_file = phase.history_dir / "spec.md"
    assert spec_file.exists(), "spec.md should be created"

    content = spec_file.read_text()
    assert "## 使用者故事" in content, "Should contain user story section"
    assert user_story in content, "Should contain the original user story"

    # Check status.json was created
    status_file = phase._get_status_file()
    assert status_file.exists(), "status.json should be created"

    # Cleanup
    import shutil
    shutil.rmtree(".aaf")
    Path(output_file).unlink()


if __name__ == "__main__":
    # Allow running directly for manual testing
    pytest.main([__file__, "-v"])
