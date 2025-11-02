"""Code review phase."""

from pathlib import Path
from typing import Optional

from aaf.agents.manager import AgentManager
from aaf.core.git import GitOperations
from aaf.core.permission import PermissionHandler
from aaf.core.phase import Phase
from aaf.core.status_codes import PhaseStatusCode, StatusCodeParser, generate_status_code_prompt
from aaf.core.types import PhaseResult, PhaseStatus, WorkflowMode


class ReviewPhase(Phase):
    """Phase 4: Code review with reviewer and developer agents."""

    def __init__(
        self,
        agent_manager: AgentManager,
        permission_handler: PermissionHandler,
        git_ops: GitOperations,
        spec_file: str,
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
            spec_file: Path to spec file
            workflow_mode: Workflow mode (local or github)
            issue_id: GitHub issue ID (required for github mode)
            review_agent: Review agent name (default: Roger)
            dev_agent: Developer agent name (default: David)
            max_iterations: Maximum review iterations (default: 3)
        """
        self.agent_manager = agent_manager
        self.permission_handler = permission_handler
        self.git_ops = git_ops
        self.spec_file = spec_file
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

                # Extract status code from review response
                status_code = StatusCodeParser.extract(
                    review_response,
                    valid_codes=[
                        PhaseStatusCode.APPROVED,
                        PhaseStatusCode.LGTM,
                        PhaseStatusCode.NEEDS_CHANGES,
                    ],
                )

                # Check if approved
                if status_code in [PhaseStatusCode.APPROVED, PhaseStatusCode.LGTM]:
                    return PhaseResult(
                        status=PhaseStatus.COMPLETED,
                        message=f"Code review passed after {self.iteration} iteration(s)",
                        data={
                            "iterations": self.iteration,
                            "review_response": review_response,
                            "status_code": status_code.value,
                        },
                    )

                # If needs changes or no status code, apply fixes and continue
                if status_code == PhaseStatusCode.NEEDS_CHANGES or status_code is None:
                    # Generate fix prompt
                    fix_prompt = self._generate_fix_prompt(review_response)

                    # Execute dev agent to fix issues
                    self.agent_manager.execute(self.dev_agent, fix_prompt)

                    # Continue to next iteration
                    continue

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

        # Generate status code prompt
        status_code_prompt = generate_status_code_prompt(
            valid_codes=[
                PhaseStatusCode.APPROVED,
                PhaseStatusCode.LGTM,
                PhaseStatusCode.NEEDS_CHANGES,
            ],
            descriptions={
                PhaseStatusCode.APPROVED: "程式碼審查通過，沒有問題",
                PhaseStatusCode.LGTM: "Looks Good To Me，程式碼審查通過",
                PhaseStatusCode.NEEDS_CHANGES: "需要修正問題",
            },
        )

        # Build base prompt
        prompt = f"""你是資深軟體工程師 {self.review_agent}，正在進行程式碼審查 (Code Review)。

這是第 {self.iteration} 輪審查（共 {self.max_iterations} 輪）。

{status_code_prompt}

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
- 如果先前的問題都已解決且沒有 critical 問題，請使用 LGTM 或 APPROVED 狀態碼
"""
        else:
            prompt += """
**你的審查任務（依優先順序）:**

1. **【最優先】檢查 commit message 風格一致性**
   - 查看本次變更的所有 commit 是否符合專案既有的 commit message 風格
   - **如果發現跟既有風格不一致：**
     - 明確指出哪些 commit SHA 的 message 不符合風格
     - 請 developer 使用以下指令修改：
       ```
       git commit --fixup=reword:<COMMIT_SHA> -m "<NEW_MESSAGE>" --allow-empty --only
       ```

2. **仔細比對需求**
   - 確認所有需求都已實作，且實作方式符合規劃

3. **找出潛在問題**
   - 檢查程式碼的正確性、可讀性、效能、安全性
   - 確認是否符合專案既有 coding style

4. **簡要說明問題**
   - 列出檔案路徑和行號並說明問題
   - 對於 commit message 問題，提供具體的修正指令
   - 不要提供程式碼解決方案（除了 commit message 修改指令）

**重要：** 用繁體中文回應。Commit message 風格問題視為 critical issue，必須修正後才能通過審查。
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
            req_path = Path(self.spec_file)
            if req_path.exists():
                return f"---\n{req_path.read_text()}\n---"
            return f"請參考 {self.spec_file}"

