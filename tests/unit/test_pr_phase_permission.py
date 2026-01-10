"""測試 PR phase 處理 NEED_PERMISSION 狀態碼"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cafe.core.types import PhaseResult, PhaseStatus, WorkflowMode
from cafe.core.phase import Phase
from cafe.phases.pr_phase import PRPhase


def test_prepare_pr_content_stops_on_need_permission(tmp_path):
    """測試：_prepare_pr_content 在收到 NEED_PERMISSION 時應該停止並回傳 result"""
    # Setup
    issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
    spec_dir = issue_dir / "spec"
    spec_dir.mkdir(parents=True)
    spec_file = spec_dir / "spec_001.md"
    spec_file.write_text("# Test Spec")
    
    plan_dir = issue_dir / "plan"
    plan_dir.mkdir(parents=True)
    plan_file = plan_dir / "plan_001.md"
    plan_file.write_text("# Test Plan")
    
    pr_dir = issue_dir / "pr"
    pr_dir.mkdir(parents=True)
    (pr_dir / "history").mkdir()

    # Create checklist.md with all items completed
    checklist_dir = pr_dir / "iteration_001"
    checklist_dir.mkdir(parents=True, exist_ok=True)
    checklist_file = checklist_dir / "checklist.md"
    checklist_file.write_text("## Execution Steps Checklist\n\n[x] Step 1\n[x] Step 2\n")

    config_file = issue_dir / "issue.yaml"
    config_file.write_text("base_branch: main\n")
    
    # Mock dependencies
    from cafe.core.types import TokenUsage
    
    agent_manager = MagicMock()
    permission_handler = MagicMock()
    git_ops = MagicMock()
    github_ops = MagicMock()
    
    git_ops.get_current_branch.return_value = "test-issue"
    git_ops.get_repo_root.return_value = tmp_path
    
    github_ops.check_gh_auth.return_value = True
    github_ops.get_pr_for_branch.return_value = None
    
    # Mock token usage to avoid validation errors
    agent_manager.get_total_token_usage.return_value = TokenUsage(
        input_tokens=100,
        output_tokens=50,
        total_cost=0.0015
    )
    
    # Mock _execute_agent_iteration to return NEED_PERMISSION
    from cafe.core.status_codes import PhaseStatusCode
    
    def mock_execute_agent_iteration(*args, **kwargs):
        return "CAFE_NEED_PERMISSION\n\n需要權限使用某些工具...", PhaseStatusCode.NEED_PERMISSION
    
    with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir), \
         patch.object(PRPhase, "_execute_agent_iteration", side_effect=mock_execute_agent_iteration), \
         patch.object(Phase, "_print_token_usage_summary"):
        # Create PR phase
        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            github_ops=github_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-issue",
            interactive=False,
        )
        
        # Call _prepare_pr_content directly
        result, content = phase._prepare_pr_content()
        
        # Verify: should return a PhaseResult (not None)
        assert result is not None
        assert isinstance(result, PhaseResult)
        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CAFE_NEED_PERMISSION"
        
        # Verify: content should be None
        assert content is None


def test_pr_phase_stops_on_need_permission(tmp_path):
    """測試：PR phase 在收到 NEED_PERMISSION 時應該停止, 不建立 PR"""
    # Setup
    issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
    spec_dir = issue_dir / "spec"
    spec_dir.mkdir(parents=True)
    spec_file = spec_dir / "spec_001.md"
    spec_file.write_text("# Test Spec")
    
    plan_dir = issue_dir / "plan"
    plan_dir.mkdir(parents=True)
    plan_file = plan_dir / "plan_001.md"
    plan_file.write_text("# Test Plan")
    
    pr_dir = issue_dir / "pr"
    pr_dir.mkdir(parents=True)
    # Create empty history dir to avoid iteration counter issues
    (pr_dir / "history").mkdir()

    # Create checklist.md with all items completed
    checklist_dir = pr_dir / "iteration_001"
    checklist_dir.mkdir(parents=True, exist_ok=True)
    checklist_file = checklist_dir / "checklist.md"
    checklist_file.write_text("## Execution Steps Checklist\n\n[x] Step 1\n[x] Step 2\n")

    config_file = issue_dir / "issue.yaml"
    config_file.write_text("base_branch: main\n")
    
    # Mock dependencies
    from cafe.core.types import TokenUsage
    
    agent_manager = MagicMock()
    permission_handler = MagicMock()
    git_ops = MagicMock()
    github_ops = MagicMock()
    
    # Mock git operations
    git_ops.get_current_branch.return_value = "test-issue"
    git_ops.get_repo_root.return_value = tmp_path
    
    # Mock GitHub operations
    github_ops.check_gh_auth.return_value = True
    github_ops.get_pr_for_branch.return_value = None  # No existing PR
    
    # Mock token usage to avoid validation errors
    agent_manager.get_total_token_usage.return_value = TokenUsage(
        input_tokens=100,
        output_tokens=50,
        total_cost=0.0015
    )
    
    # Patch to avoid issues with mocks
    from cafe.core.status_codes import PhaseStatusCode
    
    def mock_execute_agent_iteration(*args, **kwargs):
        return "CAFE_NEED_PERMISSION\n\n需要權限使用某些工具...", PhaseStatusCode.NEED_PERMISSION
    
    with patch.object(PRPhase, "_get_issue_dir", return_value=issue_dir), \
         patch.object(PRPhase, "_execute_agent_iteration", side_effect=mock_execute_agent_iteration), \
         patch.object(PRPhase, "_get_branch_name", return_value="test-issue"), \
         patch.object(Phase, "_print_token_usage_summary"):
        # Create PR phase
        phase = PRPhase(
            agent_manager=agent_manager,
            permission_handler=permission_handler,
            git_ops=git_ops,
            github_ops=github_ops,
            spec_file=str(spec_file),
            workflow_mode=WorkflowMode.LOCAL,
            issue_name="test-issue",
            interactive=False,
        )
        
        # Execute phase
        result = phase.execute()
        
        # Verify: should complete with COMPLETED status but NEED_PERMISSION code
        assert result.status == PhaseStatus.COMPLETED
        assert result.data.get("status_code") == "CAFE_NEED_PERMISSION"
        
        # Verify: github_ops.create_pr should NOT be called
        github_ops.create_pr.assert_not_called()
