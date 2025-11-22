"""Tests for interactive parameter in concrete Phase implementations."""

import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

from cafe.agents.manager import AgentManager
from cafe.core.permission import PermissionHandler
from cafe.core.git import GitOperations
from cafe.core.types import WorkflowMode
from cafe.phases.spec_phase import SpecPhase
from cafe.phases.plan_phase import PlanPhase
from cafe.phases.develop_phase import DevelopPhase
from cafe.phases.review_phase import ReviewPhase
from cafe.phases.pr_phase import PRPhase
from cafe.utils.github import GitHubOps


@pytest.fixture
def mock_github_ops():
    """Mock GitHubOps"""
    github_ops = MagicMock(spec=GitHubOps)
    return github_ops


@pytest.fixture
def mock_git_ops():
    """Mock GitOperations"""
    git_ops = MagicMock(spec=GitOperations)
    git_ops.get_current_branch.return_value = "test"
    return git_ops


class TestSpecPhaseInteractive:
    """測試 SpecPhase 的 interactive 參數"""

    def test_spec_phase_default_interactive_true(self, mock_git_ops):
        """測試 SpecPhase 預設 interactive 為 True"""
        # Arrange
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        # Act
        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
            spec_file=".cafe/issues/test/spec/spec.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

        # Assert
        assert phase.interactive is True

    def test_spec_phase_can_set_interactive_false(self, mock_git_ops):
        """測試 SpecPhase 可以設定 interactive 為 False"""
        # Arrange
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        # Act
        phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
            spec_file=".cafe/issues/test/spec/spec.md",
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        # Assert
        assert phase.interactive is False


class TestPlanPhaseInteractive:
    """測試 PlanPhase 的 interactive 參數"""

    def test_plan_phase_default_interactive_true(self, mock_git_ops):
        """測試 PlanPhase 預設 interactive 為 True"""
        # Arrange
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        # Act
        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
            spec_file=".cafe/issues/test/spec/spec.md",
            workflow_mode=WorkflowMode.LOCAL,
        )

        # Assert
        assert phase.interactive is True

    def test_plan_phase_can_set_interactive_false(self, mock_git_ops):
        """測試 PlanPhase 可以設定 interactive 為 False"""
        # Arrange
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)

        # Act
        phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=mock_git_ops,
            spec_file=".cafe/issues/test/spec/spec.md",
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )

        # Assert
        assert phase.interactive is False


class TestDevelopPhaseInteractive:
    """測試 DevelopPhase 的 interactive 參數"""

    def test_develop_phase_default_interactive_true(self):
        """測試 DevelopPhase 預設 interactive 為 True"""
        # Arrange
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)
        
        # Act
        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=".cafe/issues/test/spec/spec.md",
            plan_file=".cafe/issues/test/plan/plan.md",
            workflow_mode=WorkflowMode.LOCAL,
        )
        
        # Assert
        assert phase.interactive is True

    def test_develop_phase_can_set_interactive_false(self):
        """測試 DevelopPhase 可以設定 interactive 為 False"""
        # Arrange
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)
        
        # Act
        phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=".cafe/issues/test/spec/spec.md",
            plan_file=".cafe/issues/test/plan/plan.md",
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )
        
        # Assert
        assert phase.interactive is False


class TestReviewPhaseInteractive:
    """測試 ReviewPhase 的 interactive 參數"""

    def test_review_phase_default_interactive_true(self):
        """測試 ReviewPhase 預設 interactive 為 True"""
        # Arrange
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)
        
        # Act
        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=".cafe/issues/test/spec/spec.md",
            plan_file=".cafe/issues/test/plan/plan.md",
            workflow_mode=WorkflowMode.LOCAL,
        )
        
        # Assert
        assert phase.interactive is True

    def test_review_phase_can_set_interactive_false(self):
        """測試 ReviewPhase 可以設定 interactive 為 False"""
        # Arrange
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)
        
        # Act
        phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file=".cafe/issues/test/spec/spec.md",
            plan_file=".cafe/issues/test/plan/plan.md",
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )
        
        # Assert
        assert phase.interactive is False


class TestPRPhaseInteractive:
    """測試 PRPhase 的 interactive 參數"""

    def test_pr_phase_default_interactive_true(self, mock_github_ops):
        """測試 PRPhase 預設 interactive 為 True"""
        # Arrange
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)
        
        # Act
        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            github_ops=mock_github_ops,
            spec_file=".cafe/issues/test/spec/spec.md",
            workflow_mode=WorkflowMode.LOCAL,
        )
        
        # Assert
        assert phase.interactive is True

    def test_pr_phase_can_set_interactive_false(self, mock_github_ops):
        """測試 PRPhase 可以設定 interactive 為 False"""
        # Arrange
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = MagicMock(spec=GitOperations)
        
        # Act
        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            github_ops=mock_github_ops,
            spec_file=".cafe/issues/test/spec/spec.md",
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )
        
        # Assert
        assert phase.interactive is False


class TestInteractiveConsistency:
    """測試所有 Phase 的 interactive 行為一致性"""

    def test_all_phases_support_interactive_parameter(self, mock_github_ops, mock_git_ops):
        """測試所有 Phase 都支援 interactive 參數"""
        # Arrange
        agent_manager = MagicMock(spec=AgentManager)
        permission_handler = MagicMock(spec=PermissionHandler)
        git_ops = mock_git_ops

        # Act & Assert - 所有 Phase 都應該接受 interactive 參數
        spec_phase = SpecPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file="test.md",
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )
        assert spec_phase.interactive is False

        plan_phase = PlanPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file="test.md",
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )
        assert plan_phase.interactive is False
        
        develop_phase = DevelopPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file="test.md",
            plan_file="plan.md",
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )
        assert develop_phase.interactive is False
        
        review_phase = ReviewPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            spec_file="test.md",
            plan_file="plan.md",
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )
        assert review_phase.interactive is False
        
        pr_phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            github_ops=mock_github_ops,
            spec_file="test.md",
            workflow_mode=WorkflowMode.LOCAL,
            interactive=False,
        )
        assert pr_phase.interactive is False
