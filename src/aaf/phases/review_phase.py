"""Code review phase."""

from pathlib import Path
from typing import Optional
import json
from datetime import datetime

from aaf.agents.manager import AgentManager
from aaf.core.git import GitOperations
from aaf.core.permission import PermissionHandler
from aaf.core.phase import Phase
from aaf.core.status_codes import PhaseStatusCode, StatusCodeParser, generate_status_code_prompt
from aaf.core.types import PhaseProgress, PhaseResult, PhaseStatus, WorkflowMode


class ReviewPhase(Phase):
    """Phase 4: Code review with reviewer agent."""

    def __init__(
        self,
        agent_manager: AgentManager,
        permission_handler: PermissionHandler,
        git_ops: GitOperations,
        spec_file: str,
        workflow_mode: WorkflowMode,
        issue_id: Optional[str] = None,
        review_agent: str = "Richard",
        target_commit: Optional[str] = None,
        base_branch: str = "main",
    ) -> None:
        """Initialize review phase.

        Args:
            agent_manager: Agent manager
            permission_handler: Permission handler
            git_ops: Git operations
            spec_file: Path to spec file
            workflow_mode: Workflow mode (local or github)
            issue_id: GitHub issue ID (required for github mode)
            review_agent: Review agent name (default: Richard)
            target_commit: Specific commit to review (None for full branch)
            base_branch: Base branch for diff (default: main)
        """
        self.agent_manager = agent_manager
        self.permission_handler = permission_handler
        self.git_ops = git_ops
        self.spec_file = spec_file
        self.workflow_mode = workflow_mode
        self.issue_id = issue_id
        self.review_agent = review_agent
        self.target_commit = target_commit
        self.iteration = 1  # Track iteration number for subsequent reviews

        # Try to read base branch from issue config
        config_base_branch = self._read_base_branch_from_config()
        self.base_branch = config_base_branch if config_base_branch else base_branch

    def execute(self) -> PhaseResult:
        """Execute code review phase (single iteration).

        Returns:
            Phase result
        """
        try:
            # Get diff (full branch or specific commit)
            if self.target_commit:
                diff = self.git_ops.get_diff(
                    base=f"{self.target_commit}^", head=self.target_commit
                )
            else:
                diff = self.git_ops.get_diff(base=self.base_branch, head="HEAD")

            if not diff:
                return PhaseResult(
                    status=PhaseStatus.FAILED,
                    message="No changes found in diff",
                )

            # Generate review prompt
            review_prompt = self._generate_review_prompt(diff)

            # Execute review agent
            review_response, token_usage = self.agent_manager.execute(
                self.review_agent, review_prompt
            )

            # Extract status code from review response
            status_code = StatusCodeParser.extract(
                review_response,
                valid_codes=[
                    PhaseStatusCode.CONFIRMED,
                    PhaseStatusCode.NEEDS_CHANGES,
                ],
            )

            # Save review result
            self._save_review_result(review_response, status_code)

            # Save progress status
            if status_code:
                self._save_progress(status_code)

            # Return result based on status code
            if status_code == PhaseStatusCode.CONFIRMED:
                return PhaseResult(
                    status=PhaseStatus.COMPLETED,
                    message="Code review passed",
                    data={
                        "review_response": review_response,
                        "status_code": status_code.value,
                    },
                )
            else:
                return PhaseResult(
                    status=PhaseStatus.COMPLETED,
                    message=f"Code review completed with status: {status_code.value if status_code else 'NONE'}",
                    data={
                        "review_response": review_response,
                        "status_code": status_code.value if status_code else None,
                    },
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
                PhaseStatusCode.CONFIRMED,
                PhaseStatusCode.NEEDS_CHANGES,
            ],
            descriptions={
                PhaseStatusCode.CONFIRMED: "程式碼審查通過，沒有問題",
                PhaseStatusCode.NEEDS_CHANGES: "需要修正問題",
            },
        )

        # Build prompt
        prompt = f"""你是資深軟體工程師 {self.review_agent}，正在進行程式碼審查 (Code Review)。

{status_code_prompt}

**原始需求與實作分析:**
{requirements_section}

**程式碼變更 (diff):**
---
{diff}
---

**你的審查任務（依優先順序）:**

1. **【最優先】檢查 commit message 風格一致性**
   - 使用 `git log --oneline -10 main` 當作標準，再使用 `git log --oneline -10`，確認風格是否一致
   - **如果發現跟既有風格不一致：**
     - 明確指出哪些 commit SHA 的 message 不符合風格
     - 請 developer 使用以下指令修改：
       ```
       git commit --fixup=reword:<COMMIT_SHA> -m "<NEW_MESSAGE>" --allow-empty --only
       ```

2. **仔細比對需求**
   - 確認所有需求都已實作，且實作方式符合規劃

3. **找出潛在問題**
   - 確認是否符合專案既有 coding style
   - 檢查是否有大量重複的程式碼
   - 檢查程式碼的正確性、可讀性、效能、安全性

4. **簡要說明問題**
   - 列出檔案路徑和行號並說明問題
   - 不要提供程式碼解決方案

**重要：**
- Commit message 風格問題視為 critical issue，必須修正後才能通過審查
- 審查完成後請回傳狀態碼，指令執行即結束
"""

        return prompt

    def _save_review_result(
        self, review_response: str, status_code: Optional[PhaseStatusCode]
    ) -> None:
        """Save review result to file.

        Args:
            review_response: Review response from agent
            status_code: Status code from review
        """
        import json
        from datetime import datetime

        # Determine review directory based on workflow mode
        if self.workflow_mode == WorkflowMode.GITHUB and self.issue_id:
            review_dir = Path(f".aaf/issues/{self.issue_id}/review")
        else:
            # Extract issue name from spec_file path
            spec_path = Path(self.spec_file)
            issue_name = spec_path.parent.parent.name
            review_dir = Path(f".aaf/issues/{issue_name}/review")

        review_dir.mkdir(parents=True, exist_ok=True)
        history_dir = review_dir / "history"
        history_dir.mkdir(exist_ok=True)

        # Save latest review result
        result_file = review_dir / "review.md"
        result_file.write_text(review_response)

        # Save to history with timestamp
        timestamp = datetime.now().isoformat()
        iteration_count = len(list(history_dir.glob("iteration_*.json"))) + 1
        history_file = history_dir / f"iteration_{iteration_count:03d}.json"

        history_data = {
            "timestamp": timestamp,
            "iteration": iteration_count,
            "target_commit": self.target_commit,
            "review_response": review_response,
            "status_code": status_code.value if status_code else None,
        }

        history_file.write_text(json.dumps(history_data, ensure_ascii=False, indent=2))

    def _save_progress(self, status_code: PhaseStatusCode) -> None:
        """Save phase progress to status.json.

        Args:
            status_code: Phase status code
        """
        # Determine review directory based on workflow mode
        if self.workflow_mode == WorkflowMode.GITHUB and self.issue_id:
            review_dir = Path(f".aaf/issues/{self.issue_id}/review")
        else:
            # Extract issue name from spec_file path
            spec_path = Path(self.spec_file)
            issue_name = spec_path.parent.parent.name
            review_dir = Path(f".aaf/issues/{issue_name}/review")

        status_file = review_dir / "status.json"
        status_file.parent.mkdir(parents=True, exist_ok=True)

        # Determine phase status
        phase_status = PhaseStatus.COMPLETED if status_code == PhaseStatusCode.CONFIRMED else PhaseStatus.COMPLETED

        # Create progress object
        progress = PhaseProgress(
            phase="review",
            status=phase_status,
            status_code=status_code.value,
            iteration=self.iteration,
            timestamp=datetime.now().isoformat(),
        )

        with open(status_file, 'w', encoding='utf-8') as f:
            json.dump(progress.to_dict(), f, ensure_ascii=False, indent=2)

    def _read_base_branch_from_config(self) -> Optional[str]:
        """Read base branch from issue config file.

        Returns:
            Base branch name if found, None otherwise
        """
        # Determine config file path based on workflow mode
        if self.workflow_mode == WorkflowMode.GITHUB and self.issue_id:
            config_file = Path(f".aaf/issues/{self.issue_id}/config.json")
        else:
            # Extract issue name from spec_file path
            spec_path = Path(self.spec_file)
            issue_name = spec_path.parent.parent.name
            config_file = Path(f".aaf/issues/{issue_name}/config.json")

        if not config_file.exists():
            return None

        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
            return config_data.get("base_branch")
        except (json.JSONDecodeError, KeyError, IOError):
            return None

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

