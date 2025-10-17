"""Requirements clarification phase."""

from pathlib import Path
from typing import Optional

from aaf.agents.manager import AgentManager
from aaf.core.permission import PermissionHandler
from aaf.core.phase import Phase
from aaf.core.types import PhaseResult, PhaseStatus, WorkflowMode


class RequirementsPhase(Phase):
    """Phase 1: Requirements clarification with PM agent."""

    def __init__(
        self,
        agent_manager: AgentManager,
        permission_handler: PermissionHandler,
        requirements_file: str,
        workflow_mode: WorkflowMode,
        issue_id: Optional[str] = None,
        pm_agent: str = "Roger",
    ) -> None:
        """Initialize requirements phase.

        Args:
            agent_manager: Agent manager
            permission_handler: Permission handler
            requirements_file: Path to requirements file
            workflow_mode: Workflow mode (local or github)
            issue_id: GitHub issue ID (required for github mode)
            pm_agent: PM agent name (default: Roger)
        """
        self.agent_manager = agent_manager
        self.permission_handler = permission_handler
        self.requirements_file = requirements_file
        self.workflow_mode = workflow_mode
        self.issue_id = issue_id
        self.pm_agent = pm_agent
        self.iteration = 0

    def execute(self) -> PhaseResult:
        """Execute requirements clarification phase.

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
                req_path = Path(self.requirements_file)
                if not req_path.exists():
                    return PhaseResult(
                        status=PhaseStatus.FAILED,
                        message=f"Requirements file not found: {self.requirements_file}",
                    )

                # Backup original requirements
                self._backup_requirements(req_path)

            # Requirements clarification loop
            while True:
                self.iteration += 1

                # Generate prompt for this iteration
                prompt = self._generate_prompt()

                # Execute PM agent
                response = self.agent_manager.execute(self.pm_agent, prompt)

                # Check if requirements are confirmed
                if self.is_confirmed(response):
                    return PhaseResult(
                        status=PhaseStatus.COMPLETED,
                        message=f"Requirements clarified in {self.iteration} iteration(s)",
                        data={"iterations": self.iteration, "final_response": response},
                    )

                # Continue to next iteration if not confirmed
                # In production, this would involve user interaction

        except Exception as e:
            return PhaseResult(
                status=PhaseStatus.FAILED,
                message=f"Requirements phase failed: {e}",
            )

    def _backup_requirements(self, req_path: Path) -> None:
        """Backup original requirements file.

        Args:
            req_path: Path to requirements file
        """
        backup_path = Path(f"{req_path}.backup")
        if not backup_path.exists():
            backup_path.write_text(req_path.read_text())

    def _generate_prompt(self) -> str:
        """Generate prompt for current iteration.

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
        if self.iteration == 1:
            return f"""Use the {self.pm_agent} subagent to analyze {self.requirements_file}.

這是第 {self.iteration} 輪需求分析。

請仔細閱讀需求文件，找出所有不清楚、模糊、可能讓開發者自己腦補的地方。

**如果有需求問題需要澄清：**
用最精簡的方式條列問題，不要給任何建議。

**如果需求已經很清楚，確認完成：**
請在回應中包含：
> 需求分析狀態：已確認
"""
        else:
            return f"""Use the {self.pm_agent} subagent to continue analyzing {self.requirements_file}.

這是第 {self.iteration} 輪需求分析。

請繼續檢查需求文件的最新版本。

**如果有需求問題需要澄清：**
用最精簡的方式條列問題，不要給任何建議。

**如果需求已經很清楚，確認完成：**
請在回應中包含：
> 需求分析狀態：已確認
"""

    def _generate_github_prompt(self) -> str:
        """Generate prompt for GitHub workflow.

        Returns:
            Prompt string
        """
        if self.iteration == 1:
            return f"""Use the {self.pm_agent} subagent. 這是第 {self.iteration} 輪需求分析。

請用 `gh issue view {self.issue_id}` 讀取 Issue 內容，仔細分析需求，找出所有不清楚、模糊、可能讓開發者自己腦補的地方。

**如果有需求問題需要澄清：**
用最精簡的方式條列問題，不要給任何建議。

**如果需求已經很清楚，確認完成：**
請用戶手動在 Issue body 結尾加上一行:
> 需求分析狀態：已確認
"""
        else:
            return f"""Use the {self.pm_agent} subagent. 這是第 {self.iteration} 輪需求分析。

請用 `gh issue view {self.issue_id}` 檢視 Issue 的最新內容。

**如果有需求問題需要澄清：**
用最精簡的方式條列問題，不要給任何建議。

**如果需求已經很清楚，確認完成：**
請用戶手動在 Issue body 結尾加上一行:
> 需求分析狀態：已確認
"""

    def is_confirmed(self, response: str) -> bool:
        """Check if requirements are confirmed.

        Args:
            response: Agent response

        Returns:
            True if confirmed
        """
        return "需求分析狀態：已確認" in response
