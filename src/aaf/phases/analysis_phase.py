"""Implementation analysis phase."""

import re
from pathlib import Path
from typing import Optional

from aaf.agents.manager import AgentManager
from aaf.core.permission import PermissionHandler
from aaf.core.phase import Phase
from aaf.core.status_codes import PhaseStatusCode, StatusCodeParser, generate_status_code_prompt
from aaf.core.types import PhaseResult, PhaseStatus, WorkflowMode


class AnalysisPhase(Phase):
    """Phase 2: Implementation analysis with developer agent."""

    def __init__(
        self,
        agent_manager: AgentManager,
        permission_handler: PermissionHandler,
        requirements_file: str,
        workflow_mode: WorkflowMode,
        issue_id: Optional[str] = None,
        dev_agent: str = "David",
    ) -> None:
        """Initialize analysis phase.

        Args:
            agent_manager: Agent manager
            permission_handler: Permission handler
            requirements_file: Path to requirements file
            workflow_mode: Workflow mode (local or github)
            issue_id: GitHub issue ID (required for github mode)
            dev_agent: Developer agent name (default: David)
        """
        self.agent_manager = agent_manager
        self.permission_handler = permission_handler
        self.requirements_file = requirements_file
        self.workflow_mode = workflow_mode
        self.issue_id = issue_id
        self.dev_agent = dev_agent
        self.iteration = 0

    def execute(self) -> PhaseResult:
        """Execute implementation analysis phase.

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

                # Check for development guide
                if not self.has_dev_guide():
                    return PhaseResult(
                        status=PhaseStatus.FAILED,
                        message="需求文件中缺少「開發指南」區塊",
                    )

            # Implementation analysis loop
            while True:
                self.iteration += 1

                # Generate prompt for this iteration
                prompt = self._generate_prompt()

                # Execute developer agent
                response = self.agent_manager.execute(self.dev_agent, prompt)

                # Extract status code from response
                status_code = StatusCodeParser.extract(
                    response,
                    valid_codes=[
                        PhaseStatusCode.CONFIRMED,
                        PhaseStatusCode.NEED_CLARIFICATION,
                        PhaseStatusCode.REJECTED,
                    ],
                )

                # Handle status codes
                if status_code == PhaseStatusCode.CONFIRMED:
                    return PhaseResult(
                        status=PhaseStatus.COMPLETED,
                        message=f"Implementation analysis completed in {self.iteration} iteration(s)",
                        data={
                            "iterations": self.iteration,
                            "final_response": response,
                            "status_code": status_code.value,
                        },
                    )
                elif status_code == PhaseStatusCode.REJECTED:
                    return PhaseResult(
                        status=PhaseStatus.FAILED,
                        message=f"Implementation analysis rejected in iteration {self.iteration}",
                        data={
                            "iterations": self.iteration,
                            "final_response": response,
                            "status_code": status_code.value,
                        },
                    )
                elif status_code == PhaseStatusCode.NEED_CLARIFICATION:
                    # Continue to next iteration for clarification
                    continue
                else:
                    # No valid status code found, continue iteration
                    continue

        except Exception as e:
            return PhaseResult(
                status=PhaseStatus.FAILED,
                message=f"Analysis phase failed: {e}",
            )

    def has_dev_guide(self) -> bool:
        """Check if requirements file has development guide section.

        Returns:
            True if development guide exists
        """
        req_path = Path(self.requirements_file)
        if not req_path.exists():
            return False

        content = req_path.read_text()
        # Check for various heading formats
        patterns = [
            r"##\s*開發指南",
            r"##\s*[Dd]evelopment\s+[Gg]uide",
            r"###\s*開發指南",
            r"###\s*[Dd]evelopment\s+[Gg]uide",
        ]

        for pattern in patterns:
            if re.search(pattern, content, re.IGNORECASE):
                return True

        return False

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
        status_code_prompt = generate_status_code_prompt(
            valid_codes=[
                PhaseStatusCode.CONFIRMED,
                PhaseStatusCode.NEED_CLARIFICATION,
                PhaseStatusCode.REJECTED,
            ],
            descriptions={
                PhaseStatusCode.CONFIRMED: "實作分析已完成，可以開始開發",
                PhaseStatusCode.NEED_CLARIFICATION: "需要更多資訊或確認",
                PhaseStatusCode.REJECTED: "實作分析無法進行",
            },
        )

        if self.iteration == 1:
            return f"""Use the {self.dev_agent} subagent to analyze {self.requirements_file}.

這是第 {self.iteration} 輪實作分析。

請仔細閱讀需求文件和開發指南，規劃詳細的實作步驟。

{status_code_prompt}

**如果需要更多資訊：**
列出需要確認的問題。

**如果分析完成：**
回應確認訊息。
"""
        else:
            return f"""Use the {self.dev_agent} subagent to continue analyzing {self.requirements_file}.

這是第 {self.iteration} 輪實作分析。

請繼續檢查實作計畫。

{status_code_prompt}

**如果需要更多資訊：**
列出需要確認的問題。

**如果分析完成：**
回應確認訊息。
"""

    def _generate_github_prompt(self) -> str:
        """Generate prompt for GitHub workflow.

        Returns:
            Prompt string
        """
        status_code_prompt = generate_status_code_prompt(
            valid_codes=[
                PhaseStatusCode.CONFIRMED,
                PhaseStatusCode.NEED_CLARIFICATION,
                PhaseStatusCode.REJECTED,
            ],
            descriptions={
                PhaseStatusCode.CONFIRMED: "實作分析已完成，可以開始開發",
                PhaseStatusCode.NEED_CLARIFICATION: "需要更多資訊或確認",
                PhaseStatusCode.REJECTED: "實作分析無法進行",
            },
        )

        if self.iteration == 1:
            return f"""Use the {self.dev_agent} subagent. 這是第 {self.iteration} 輪實作分析。

請用 `gh issue view {self.issue_id}` 讀取 Issue 內容，根據需求和開發指南規劃詳細的實作步驟。

{status_code_prompt}

**如果需要更多資訊：**
用 `gh issue comment {self.issue_id}` 發 comment 詢問。

**如果分析完成：**
回應確認訊息。
"""
        else:
            return f"""Use the {self.dev_agent} subagent. 這是第 {self.iteration} 輪實作分析。

請用 `gh issue view {self.issue_id}` 檢視 Issue 的最新內容。

{status_code_prompt}

**如果需要更多資訊：**
用 `gh issue comment {self.issue_id}` 發 comment 詢問。

**如果分析完成：**
回應確認訊息。
"""

