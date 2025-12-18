"""測試 DevelopPhase  review feedback 檢查邏輯."""

import json
import pytest
from pathlib import Path
from unittest.mock import Mock, patch

from cafe.phases.develop_phase import DevelopPhase
from cafe.core.types import WorkflowMode


@pytest.fixture
def mock_components(tmp_path):
    """建立測試用 mock 元件."""
    # Create issue directory structure
    issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
    spec_dir = issue_dir / "spec"
    plan_dir = issue_dir / "plan"
    review_dir = issue_dir / "review"

    spec_dir.mkdir(parents=True, exist_ok=True)
    plan_dir.mkdir(parents=True, exist_ok=True)
    review_dir.mkdir(parents=True, exist_ok=True)

    spec_file = spec_dir / "spec_001.md"
    plan_file = plan_dir / "plan_001.md"

    spec_file.write_text("Test spec")
    plan_file.write_text("Test plan")

    mock_agent_manager = Mock()
    mock_permission_handler = Mock()
    mock_git_ops = Mock()

    return {
        "tmp_path": tmp_path,
        "issue_dir": issue_dir,
        "review_dir": review_dir,
        "spec_file": str(spec_file),
        "plan_file": str(plan_file),
        "agent_manager": mock_agent_manager,
        "permission_handler": mock_permission_handler,
        "git_ops": mock_git_ops,
    }


class TestDevelopPhaseReviewFeedbackCheck:
    """測試 _check_review_feedback_exists 方法"""

    def test_no_review_file_returns_false(self, mock_components):
        """測試當沒有 review file 時返回 False

        情境：review 目錄是空
        預期：_check_review_feedback_exists() 返回 False
        """
        phase = DevelopPhase(
            agent_manager=mock_components["agent_manager"],
            permission_handler=mock_components["permission_handler"],
            git_ops=mock_components["git_ops"],
            spec_file=mock_components["spec_file"],
            plan_file=mock_components["plan_file"],
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-issue",
            dev_agent="David",
        )

        assert phase._check_review_feedback_exists() is False

    def test_review_file_exists_with_confirmed_status_returns_false(self, mock_components):
        """測試當 review file 存在但狀態是 CONFIRMED 時返回 False

        情境：有 review file, 但 review 已經 CONFIRMED（已通過）
        預期：_check_review_feedback_exists() 返回 False（不需要修正）
        """
        # 建立 review file
        review_file = mock_components["review_dir"] / "review_001.md"
        review_file.write_text("Review feedback - already confirmed")

        # 建立 status.json, 狀態為 CONFIRMED
        status_file = mock_components["review_dir"] / "status.json"
        status_file.write_text(json.dumps({
            "phase": "review",
            "status": "completed",
            "status_code": "CAFE_CONFIRMED",
            "timestamp": "2025-12-10T10:00:00",
            "iteration": 1
        }))

        phase = DevelopPhase(
            agent_manager=mock_components["agent_manager"],
            permission_handler=mock_components["permission_handler"],
            git_ops=mock_components["git_ops"],
            spec_file=mock_components["spec_file"],
            plan_file=mock_components["plan_file"],
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-issue",
            dev_agent="David",
        )

        assert phase._check_review_feedback_exists() is False

    def test_review_file_exists_with_needs_changes_status_returns_true(self, mock_components):
        """測試當 review file 存在且狀態是 NEEDS_CHANGES 時返回 True

        情境：有 review file, 且 review 狀態是 NEEDS_CHANGES（需要修正）
        預期：_check_review_feedback_exists() 返回 True
        """
        # 建立 review file
        review_file = mock_components["review_dir"] / "review_001.md"
        review_file.write_text("Review feedback - needs changes")

        # 建立 status.json, 狀態為 NEEDS_CHANGES
        status_file = mock_components["review_dir"] / "status.json"
        status_file.write_text(json.dumps({
            "phase": "review",
            "status": "completed",
            "status_code": "CAFE_NEEDS_CHANGES",
            "timestamp": "2025-12-10T10:00:00",
            "iteration": 1
        }))

        phase = DevelopPhase(
            agent_manager=mock_components["agent_manager"],
            permission_handler=mock_components["permission_handler"],
            git_ops=mock_components["git_ops"],
            spec_file=mock_components["spec_file"],
            plan_file=mock_components["plan_file"],
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-issue",
            dev_agent="David",
        )

        assert phase._check_review_feedback_exists() is True

    def test_review_file_exists_without_status_file_returns_true(self, mock_components):
        """測試當 review file 存在但沒有 status.json 時返回 True

        情境：有 review file, 但沒有 status.json（可能是舊版本or異常情況）
        預期：_check_review_feedback_exists() 返回 True（保守處理, 視為需要處理）
        """
        # 建立 review file（沒有 status.json）
        review_file = mock_components["review_dir"] / "review_001.md"
        review_file.write_text("Review feedback - no status file")

        phase = DevelopPhase(
            agent_manager=mock_components["agent_manager"],
            permission_handler=mock_components["permission_handler"],
            git_ops=mock_components["git_ops"],
            spec_file=mock_components["spec_file"],
            plan_file=mock_components["plan_file"],
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-issue",
            dev_agent="David",
        )

        assert phase._check_review_feedback_exists() is True
