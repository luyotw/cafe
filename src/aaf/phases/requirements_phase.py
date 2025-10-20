"""Requirements clarification phase."""

from pathlib import Path
from typing import Optional

from aaf.agents.manager import AgentManager
from aaf.core.permission import PermissionHandler
from aaf.core.phase import Phase
from aaf.core.status_codes import PhaseStatusCode, StatusCodeParser, generate_status_code_prompt
from aaf.core.types import PhaseResult, PhaseStatus, WorkflowMode


def create_github_issue(content: str) -> str:
    """Create a new GitHub issue with content.

    Args:
        content: Issue content

    Returns:
        Issue ID

    Note:
        This is a placeholder. Actual implementation should use gh CLI.
    """
    # TODO: Implement using gh CLI
    # gh issue create --title "..." --body "..."
    raise NotImplementedError("GitHub issue creation not yet implemented")


def update_github_issue(issue_id: str, content: str) -> None:
    """Update existing GitHub issue.

    Args:
        issue_id: Issue ID
        content: Updated content

    Note:
        This is a placeholder. Actual implementation should use gh CLI.
    """
    # TODO: Implement using gh CLI
    # gh issue edit <issue_id> --body "..."
    raise NotImplementedError("GitHub issue update not yet implemented")


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
            # Note: GitHub mode can now work without issue_id (will create new issue)

            if self.workflow_mode == WorkflowMode.LOCAL:
                # Check if requirements file exists
                req_path = Path(self.requirements_file)
                if req_path.exists():
                    # Backup original requirements if exists
                    self._backup_requirements(req_path)
                # If file doesn't exist, we'll generate it through conversation

            # Requirements clarification loop
            while True:
                self.iteration += 1

                # Generate prompt for this iteration
                prompt = self._generate_prompt()

                # Execute PM agent
                response = self.agent_manager.execute(self.pm_agent, prompt)

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
                    # Save generated requirements
                    self._save_requirements(response)

                    result_data = {
                        "iterations": self.iteration,
                        "final_response": response,
                        "status_code": status_code.value,
                    }

                    # Add issue_id if GitHub mode and created new issue
                    if self.workflow_mode == WorkflowMode.GITHUB and hasattr(self, '_created_issue_id'):
                        result_data["issue_id"] = self._created_issue_id

                    return PhaseResult(
                        status=PhaseStatus.COMPLETED,
                        message=f"Requirements clarified in {self.iteration} iteration(s)",
                        data=result_data,
                    )
                elif status_code == PhaseStatusCode.REJECTED:
                    return PhaseResult(
                        status=PhaseStatus.FAILED,
                        message=f"Requirements rejected in iteration {self.iteration}",
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

    def _save_requirements(self, response: str) -> None:
        """Save generated requirements to file or GitHub issue.

        Args:
            response: Agent response containing requirements
        """
        if self.workflow_mode == WorkflowMode.LOCAL:
            # Save to local file
            req_path = Path(self.requirements_file)
            req_path.parent.mkdir(parents=True, exist_ok=True)
            req_path.write_text(response)
        elif self.workflow_mode == WorkflowMode.GITHUB:
            # Create or update GitHub issue
            if not self.issue_id:
                # Create new issue
                self._created_issue_id = create_github_issue(response)
            else:
                # Update existing issue
                update_github_issue(self.issue_id, response)

    def _create_github_issue(self, content: str) -> str:
        """Create a new GitHub issue with requirements.

        Args:
            content: Requirements content

        Returns:
            Issue ID
        """
        return create_github_issue(content)

    def _update_github_issue(self, content: str) -> None:
        """Update existing GitHub issue with requirements.

        Args:
            content: Requirements content
        """
        update_github_issue(self.issue_id, content)

    def _generate_prompt(self) -> str:
        """Generate prompt for current iteration.

        Returns:
            Prompt string
        """
        if self.workflow_mode == WorkflowMode.GITHUB:
            return self._generate_github_prompt()
        else:
            return self._generate_local_prompt()

    def _get_non_technical_guidelines(self) -> str:
        """Get non-technical guidelines for PM.

        Returns:
            Guidelines string
        """
        return """**重要：絕對不可涉及技術細節！**
- ❌ 不要提及實作方式、技術架構、程式語言、框架、資料庫等
- ❌ 不要建議任何技術解決方案
- ✅ 只關注「用戶要什麼」「為什麼要」「預期效果是什麼」
- ✅ 從產品和業務角度思考"""

    def _get_status_code_prompt(self) -> str:
        """Get status code prompt.

        Returns:
            Status code prompt string
        """
        return generate_status_code_prompt(
            valid_codes=[
                PhaseStatusCode.CONFIRMED,
                PhaseStatusCode.NEED_CLARIFICATION,
                PhaseStatusCode.REJECTED,
            ],
            descriptions={
                PhaseStatusCode.CONFIRMED: "需求已經很清楚，可以進行開發",
                PhaseStatusCode.NEED_CLARIFICATION: "需求有不清楚的地方需要澄清",
                PhaseStatusCode.REJECTED: "需求有嚴重問題，無法進行",
            },
        )

    def _generate_local_prompt(self) -> str:
        """Generate prompt for local workflow.

        Returns:
            Prompt string
        """
        non_technical = self._get_non_technical_guidelines()
        status_code_prompt = self._get_status_code_prompt()

        # Check if requirements file exists
        req_path = Path(self.requirements_file)
        file_exists = req_path.exists()

        if self.iteration == 1:
            if file_exists:
                # File exists - analyze and clarify
                return f"""分析 {self.requirements_file} 的內容。

你是 PM，負責與用戶溝通並產出完整的需求文件。這是第 {self.iteration} 輪需求澄清。

**你的職責：**
1. 閱讀需求文件，找出所有不清楚、模糊、缺失的資訊
2. **以對話方式**向用戶提問，確認所有必要資訊
3. 根據用戶回應更新需求文件

{non_technical}

{status_code_prompt}

**如果需要澄清需求（status: NEED_CLARIFICATION）：**
以對話方式向用戶提問，例如：
- 這個功能的目的是什麼？
- 用戶預期看到什麼結果？
- 有哪些使用場景？
記住：不要提技術細節！

**如果需求已清楚（status: CONFIRMED）：**
確認需求文件已更新完整，包含：功能描述、使用場景、預期行為、驗收標準。
"""
            else:
                # File doesn't exist - start from scratch
                return f"""你是 PM，負責與用戶溝通並產出完整的需求文件。這是第 {self.iteration} 輪需求澄清。

**目前狀況：尚無需求文件，需要從零開始。**

**你的職責：**
1. **以對話方式**向用戶詢問他們想要什麼功能
2. 透過提問確認所有必要資訊
3. 最後產出完整的需求文件

{non_technical}

{status_code_prompt}

**如果需要更多資訊（status: NEED_CLARIFICATION）：**
以親切的對話方式向用戶提問，例如：
- 您想要實作什麼功能？
- 這個功能的主要目的是什麼？
- 用戶會如何使用這個功能？
- 預期看到什麼結果？
記住：不要提技術細節！

**如果資訊已足夠（status: CONFIRMED）：**
產出完整的需求文件，包含：功能描述、使用場景、預期行為、驗收標準。
"""
        else:
            return f"""繼續分析 {self.requirements_file} 的最新版本。

這是第 {self.iteration} 輪需求澄清。請檢查需求文件的最新版本。

{non_technical}

{status_code_prompt}

**如果仍需澄清（status: NEED_CLARIFICATION）：**
繼續以對話方式提問，確認缺失的資訊。

**如果需求已清楚（status: CONFIRMED）：**
確認需求文件完整且無技術細節。
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
                PhaseStatusCode.CONFIRMED: "需求已經很清楚，可以進行開發",
                PhaseStatusCode.NEED_CLARIFICATION: "需求有不清楚的地方需要澄清",
                PhaseStatusCode.REJECTED: "需求有嚴重問題，無法進行",
            },
        )

        if self.iteration == 1:
            return f"""這是第 {self.iteration} 輪需求分析。

請用 `gh issue view {self.issue_id}` 讀取 Issue 內容，仔細分析需求，找出所有不清楚、模糊、可能讓開發者自己腦補的地方。

{status_code_prompt}

**如果有需求問題需要澄清：**
用最精簡的方式條列問題，不要給任何建議。

**如果需求已經很清楚，確認完成：**
回應確認訊息。
"""
        else:
            return f"""這是第 {self.iteration} 輪需求分析。

請用 `gh issue view {self.issue_id}` 檢視 Issue 的最新內容。

{status_code_prompt}

**如果有需求問題需要澄清：**
用最精簡的方式條列問題，不要給任何建議。

**如果需求已經很清楚，確認完成：**
回應確認訊息。
"""

