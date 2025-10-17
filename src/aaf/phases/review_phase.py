"""Code review phase."""

from pathlib import Path
from typing import Optional

from aaf.agents.manager import AgentManager
from aaf.core.git import GitOperations
from aaf.core.permission import PermissionHandler
from aaf.core.phase import Phase
from aaf.core.types import PhaseResult, PhaseStatus, WorkflowMode


class ReviewPhase(Phase):
    """Phase 4: Code review with reviewer and developer agents."""

    def __init__(
        self,
        agent_manager: AgentManager,
        permission_handler: PermissionHandler,
        git_ops: GitOperations,
        requirements_file: str,
        workflow_mode: WorkflowMode,
        issue_id: Optional[str] = None,
        review_agent: str = "Roger",
        dev_agent: str = "David",
        max_iterations: int = 3,
    ) -> None:
        """Initialize review phase.

        Args:
            agent_manager: Agent manager
            permission_handler: Permission handler
            git_ops: Git operations
            requirements_file: Path to requirements file
            workflow_mode: Workflow mode (local or github)
            issue_id: GitHub issue ID (required for github mode)
            review_agent: Review agent name (default: Roger)
            dev_agent: Developer agent name (default: David)
            max_iterations: Maximum review iterations (default: 3)
        """
        self.agent_manager = agent_manager
        self.permission_handler = permission_handler
        self.git_ops = git_ops
        self.requirements_file = requirements_file
        self.workflow_mode = workflow_mode
        self.issue_id = issue_id
        self.review_agent = review_agent
        self.dev_agent = dev_agent
        self.max_iterations = max_iterations
        self.iteration = 0

    def execute(self) -> PhaseResult:
        """Execute code review phase.

        Returns:
            Phase result
        """
        try:
            # Review-fix loop
            while self.iteration < self.max_iterations:
                self.iteration += 1

                # Get diff
                diff = self.git_ops.get_diff(base="main", head="HEAD")
                if not diff:
                    return PhaseResult(
                        status=PhaseStatus.FAILED,
                        message="No changes found in diff",
                    )

                # Generate review prompt
                review_prompt = self._generate_review_prompt(diff)

                # Execute review agent
                review_response = self.agent_manager.execute(
                    self.review_agent, review_prompt
                )

                # Check if approved
                if self.is_approved(review_response):
                    return PhaseResult(
                        status=PhaseStatus.COMPLETED,
                        message=f"Code review passed after {self.iteration} iteration(s)",
                        data={
                            "iterations": self.iteration,
                            "review_response": review_response,
                        },
                    )

                # Generate fix prompt
                fix_prompt = self._generate_fix_prompt(review_response)

                # Execute dev agent to fix issues
                self.agent_manager.execute(self.dev_agent, fix_prompt)

                # Continue to next iteration

            # Max iterations reached
            return PhaseResult(
                status=PhaseStatus.COMPLETED,
                message=f"Code review completed after {self.max_iterations} iterations (max reached)",
                data={"iterations": self.max_iterations},
            )

        except Exception as e:
            return PhaseResult(
                status=PhaseStatus.FAILED,
                message=f"Review phase failed: {e}",
            )

    def _generate_review_prompt(self, diff: str) -> str:
        """Generate review prompt.

        Args:
            diff: Git diff content

        Returns:
            Review prompt string
        """
        # Get requirements section
        requirements_section = self._get_requirements_section()

        # Build base prompt
        prompt = f"""你是資深軟體工程師 {self.review_agent}，正在進行程式碼審查 (Code Review)。

這是第 {self.iteration} 輪審查（共 {self.max_iterations} 輪）。

**原始需求與實作分析:**
{requirements_section}

**程式碼變更 (diff):**
---
{diff}
---
"""

        # Add history hint for subsequent reviews
        if self.iteration > 1:
            prompt += """
**重要審查原則：**
- 這不是第一次審查，請先參考先前提出的問題
- **優先檢查：** 先前提出的問題是否已修正
- **新問題限制：** 只提出 critical 問題（嚴重 bug、安全性問題、功能缺失）
- **避免：** 不要提出風格、命名、小優化等非必要的建議
- 如果先前的問題都已解決且沒有 critical 問題，請給予 LGTM
"""
        else:
            prompt += """
**你的審查任務:**
- **檢查 commit message**：確認 commit message 只有一行。
- **仔細比對需求**：確認所有需求都已實作，且實作方式符合規劃。
- **找出潛在問題**：檢查程式碼的正確性、可讀性、效能、安全性、以及是否符合專案既有風格。
- **簡要說明問題**：列出檔案路徑和行號並說明問題，不要提供任何解決方案。
- **用繁體中文回應**。

**如果沒有問題，請回覆 "LGTM" (Looks Good To Me)。**
"""

        return prompt

    def _generate_fix_prompt(self, review_feedback: str) -> str:
        """Generate fix prompt for developer agent.

        Args:
            review_feedback: Review feedback from review agent

        Returns:
            Fix prompt string
        """
        return f"""Use the {self.dev_agent} subagent to fix review issues.

Reviewer 提出了以下問題，請修正：

---
{review_feedback}
---

請根據 review 意見修正程式碼並 commit。
"""

    def _get_requirements_section(self) -> str:
        """Get requirements section for review prompt.

        Returns:
            Requirements section string
        """
        if self.workflow_mode == WorkflowMode.GITHUB:
            return f"請用 `gh issue view {self.issue_id}` 查看 Issue 內容（包含需求與實作分析）。"
        else:
            req_path = Path(self.requirements_file)
            if req_path.exists():
                return f"---\n{req_path.read_text()}\n---"
            return f"請參考 {self.requirements_file}"

    def is_approved(self, response: str) -> bool:
        """Check if code review is approved.

        Args:
            response: Review response

        Returns:
            True if approved (contains LGTM)
        """
        response_lower = response.lower()
        return "lgtm" in response_lower or "looks good to me" in response_lower
