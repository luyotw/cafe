"""Development phase."""

import re
from pathlib import Path
from typing import Optional

from aaf.agents.manager import AgentManager
from aaf.core.git import GitOperations
from aaf.core.permission import PermissionHandler
from aaf.core.phase import Phase
from aaf.core.types import PhaseResult, PhaseStatus, WorkflowMode


class DevelopPhase(Phase):
    """Phase 3: Development with developer agent."""

    def __init__(
        self,
        agent_manager: AgentManager,
        permission_handler: PermissionHandler,
        git_ops: GitOperations,
        spec_file: str,
        workflow_mode: WorkflowMode,
        issue_id: Optional[str] = None,
        dev_agent: str = "David",
    ) -> None:
        """Initialize develop phase.

        Args:
            agent_manager: Agent manager
            permission_handler: Permission handler
            git_ops: Git operations
            spec_file: Path to spec file
            workflow_mode: Workflow mode (local or github)
            issue_id: GitHub issue ID (required for github mode)
            dev_agent: Developer agent name (default: David)
        """
        self.agent_manager = agent_manager
        self.permission_handler = permission_handler
        self.git_ops = git_ops
        self.spec_file = spec_file
        self.workflow_mode = workflow_mode
        self.issue_id = issue_id
        self.dev_agent = dev_agent

    def execute(self) -> PhaseResult:
        """Execute development phase.

        Returns:
            Phase result
        """
        try:
            # Validate inputs
            if self.workflow_mode == WorkflowMode.GITHUB and not self.issue_id:
                return PhaseResult(
                    status=PhaseStatus.FAILED,
                    message="GitHub mode requires issue_id",
                )

            if self.workflow_mode == WorkflowMode.LOCAL:
                # Check requirements file exists
                req_path = Path(self.spec_file)
                if not req_path.exists():
                    return PhaseResult(
                        status=PhaseStatus.FAILED,
                        message=f"Spec file not found: {self.spec_file}",
                    )

            # Create or checkout branch
            branch_name = self._get_branch_name()
            if self.git_ops.branch_exists(branch_name):
                self.git_ops.checkout_branch(branch_name)
            else:
                self.git_ops.create_branch(branch_name)

            # Generate development prompt
            prompt = self._generate_prompt()

            # Execute developer agent
            response = self.agent_manager.execute(self.dev_agent, prompt)

            return PhaseResult(
                status=PhaseStatus.COMPLETED,
                message=f"Development completed on branch {branch_name}",
                data={"branch": branch_name, "response": response},
            )

        except Exception as e:
            return PhaseResult(
                status=PhaseStatus.FAILED,
                message=f"Development phase failed: {e}",
            )

    def _get_branch_name(self) -> str:
        """Get branch name based on workflow mode.

        Returns:
            Branch name
        """
        if self.workflow_mode == WorkflowMode.GITHUB:
            return f"issue-{self.issue_id}"
        else:
            # Extract from requirements filename
            # e.g., "20250101-feature.md" -> "feature"
            filename = Path(self.spec_file).stem
            # Remove date prefix if exists
            match = re.match(r"^\d{8}-(.+)$", filename)
            if match:
                return match.group(1)
            return filename

    def _generate_prompt(self) -> str:
        """Generate development prompt.

        Returns:
            Prompt string
        """
        if self.workflow_mode == WorkflowMode.GITHUB:
            return self._generate_github_prompt()
        else:
            return self._generate_local_prompt()

    def _generate_local_prompt(self) -> str:
        """Generate prompt for local workflow.

        Returns:
            Prompt string
        """
        return f"""Use the {self.dev_agent} subagent for development.

需求已經確認清楚，請根據 {self.spec_file} 進行開發。

請執行以下步驟：
1. 嚴格按照開發任務拆解的順序進行開發及測試
2. 直接使用開發任務中的 commit message，禁止加入新的內容
3. commit 完之後在 {self.spec_file} 中將已完成的項目打勾

**注意：先不要 push 到 remote，等 code review 完成後再 push！**
"""

    def _generate_github_prompt(self) -> str:
        """Generate prompt for GitHub workflow.

        Returns:
            Prompt string
        """
        return f"""Use the {self.dev_agent} subagent for development.

需求已經確認清楚，請用 `gh issue view {self.issue_id}` 查看 Issue 中的需求和實作分析進行開發。

請執行以下步驟：
1. 嚴格按照開發任務拆解的順序進行開發及測試
2. 直接使用開發任務中的 commit message，禁止加入新的內容
3. commit 完之後使用 `gh issue comment {self.issue_id}` 發 comment，用最簡單的文字說明進度，例如："已完成 Task 3 並 commit。"

**注意：先不要 push 到 remote，等 code review 完成後再 push！**
"""
