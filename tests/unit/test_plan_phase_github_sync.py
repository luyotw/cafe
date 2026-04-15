from pathlib import Path
from unittest.mock import MagicMock, patch

from cafe.phases.plan_phase import PlanPhase


def test_plan_phase_no_longer_exposes_internal_github_sync_hook(tmp_path: Path) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
    issue_dir.mkdir(parents=True, exist_ok=True)

    with patch.object(PlanPhase, "_get_issue_dir", return_value=issue_dir):
        phase = PlanPhase(
            agent_manager=MagicMock(),
            permission_handler=MagicMock(),
            git_ops=MagicMock(),
            spec_file="spec.md",
            issue_name="test-issue",
            interactive=False,
        )

    assert not hasattr(phase, "_sync_plan_to_github")