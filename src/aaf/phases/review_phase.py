"""Code review phase."""

from pathlib import Path
from typing import List, Optional
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
        plan_file: str,
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
            plan_file: Path to plan file
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
        self.plan_file = plan_file
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

            # Get agent executor info for history
            agent_executor = self.agent_manager.get_agent(self.review_agent)
            agent_cli = agent_executor.config.cli.value
            agent_session_id = agent_executor.config.session_id

            # Extract status code from review response
            status_code = StatusCodeParser.extract(
                review_response,
                valid_codes=[
                    PhaseStatusCode.CONFIRMED,
                    PhaseStatusCode.NEEDS_CHANGES,
                ],
            )

            # Save review result with agent info
            self._save_review_result(
                review_response,
                status_code,
                prompt=review_prompt,
                agent_cli=agent_cli,
                agent_session_id=agent_session_id
            )

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

    def _check_if_develop_is_newer(self) -> bool:
        """檢查 develop phase 的時間戳記是否比上次 review 更新。

        Returns:
            True 如果 develop 更新（需要重新執行所有檢查），False 否則
        """
        try:
            # 取得 issue name
            spec_path = Path(self.spec_file).resolve()
            issue_name = spec_path.parent.parent.name

            # 讀取 develop/status.json（使用相對於 spec_file 的路徑）
            issue_dir = spec_path.parent.parent
            develop_status_file = issue_dir / "develop" / "status.json"
            if not develop_status_file.exists():
                return False

            # 讀取 review/status.json
            review_status_file = issue_dir / "review" / "status.json"
            if not review_status_file.exists():
                # 第一次 review，需要重新執行所有檢查
                return True

            # 比較時間戳記
            with open(develop_status_file) as f:
                develop_data = json.load(f)
            with open(review_status_file) as f:
                review_data = json.load(f)

            develop_time = datetime.fromisoformat(develop_data["timestamp"])
            review_time = datetime.fromisoformat(review_data["timestamp"])

            # 如果 develop 的時間比 review 新，說明有新的變更
            return develop_time > review_time

        except Exception:
            # 如果出錯，保守起見返回 True（重新執行檢查）
            return True

    def _generate_review_prompt(self, diff: str) -> str:
        """Generate review prompt.

        Args:
            diff: Git diff content

        Returns:
            Review prompt string
        """
        # Get requirements section
        requirements_section = self._get_requirements_section()

        # 檢查是否需要重新執行檢查（develop 比 review 新）
        develop_is_newer = self._check_if_develop_is_newer()
        recheck_instruction = ""
        if develop_is_newer:
            recheck_instruction = """
**【重要提示】develop phase 在上次 review 之後有新的變更，請重新執行所有檢查：**
- **必須重新執行 git log 指令**，不要使用之前的快取結果
- 檢查最新的 commit messages 和程式碼變更
- 這是一次全新的審查，請忽略之前的審查記錄

"""

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
{recheck_instruction}
**需求規格與實作計畫:**
{requirements_section}

**程式碼變更 (diff):**
---
{diff}
---

**你的審查任務（依優先順序）:**

1. **【最優先】檢查 commit message 風格一致性**
   - 使用 `git --no-pager log --oneline -10 {self.base_branch}` 查看基礎分支的 commit 風格（作為標準）
   - 使用 `git --no-pager log --oneline -10` 查看當前分支的 commit
   - 確認風格是否一致
   - **如果發現風格不一致：**
     - 明確列出哪些 commit SHA 和 message 不符合風格
     - 說明正確的風格範例（根據基礎分支的實際風格）
     - **重要：提供完整的 shell 指令讓 developer 直接執行（每個 commit 一條），禁止使用專案目錄外的檔案路徑**
     - **Developer 可以直接執行這些指令，不需要請求權限，也不要用互動式 rebase**
     
     指令範例：
     ```bash
     # 修改 commit abc123 的 message
     echo "Fix login logic" > ./commit_msg.txt && \\
     git rebase --onto {self.base_branch} {self.base_branch} HEAD --exec '
       if test $(git rev-parse HEAD) = abc123 || test $(git rev-parse HEAD) = $(git rev-parse abc123); then
         git commit --amend -F ./commit_msg.txt --allow-empty --no-edit;
       fi
     ' && rm -f ./commit_msg.txt
     ```

2. **仔細比對需求及實作文件**
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
        self,
        review_response: str,
        status_code: Optional[PhaseStatusCode],
        prompt: Optional[str] = None,
        agent_cli: Optional[str] = None,
        agent_session_id: Optional[str] = None,
        allowed_tools: Optional[List[str]] = None,
        denied_tools: Optional[List[str]] = None
    ) -> None:
        """Save review result to file.

        Args:
            review_response: Review response from agent
            status_code: Status code from review
            prompt: The actual prompt sent to the agent
            agent_cli: CLI tool used by the agent (e.g., "copilot", "claude")
            agent_session_id: Session ID of the agent
            allowed_tools: List of allowed tools for the agent
            denied_tools: List of denied tools for the agent
        """
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

        # Calculate iteration count
        iteration_count = len(list(history_dir.glob("iteration_*.json"))) + 1

        # Temporarily set iteration and history_dir for base class method
        saved_iteration = getattr(self, 'iteration', None)
        saved_history_dir = getattr(self, 'history_dir', None)

        self.iteration = iteration_count
        self.history_dir = history_dir

        # Use base class method with ReviewPhase-specific data
        super()._save_iteration_history(
            phase_specific_data={
                "target_commit": self.target_commit,
                "review_response": review_response,
            },
            prompt=prompt,
            agent_cli=agent_cli,
            agent_session_id=agent_session_id,
            allowed_tools=allowed_tools,
            denied_tools=denied_tools,
            status_code=status_code,
        )

        # Restore original values
        if saved_iteration is not None:
            self.iteration = saved_iteration
        if saved_history_dir is not None:
            self.history_dir = saved_history_dir

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
        """Get requirements and plan section for review prompt.

        Returns:
            Requirements and plan section string
        """
        if self.workflow_mode == WorkflowMode.GITHUB:
            return f"請用 `gh issue view {self.issue_id}` 查看 Issue 內容（包含需求與實作分析）。"
        else:
            sections = []
            
            # Add spec
            spec_path = Path(self.spec_file)
            if spec_path.exists():
                sections.append(f"**需求規格 (Spec):**\n---\n{spec_path.read_text()}\n---")
            else:
                sections.append(f"**需求規格 (Spec):** 請參考 {self.spec_file}")
            
            # Add plan
            plan_path = Path(self.plan_file)
            if plan_path.exists():
                sections.append(f"**實作計畫 (Plan):**\n---\n{plan_path.read_text()}\n---")
            else:
                sections.append(f"**實作計畫 (Plan):** 請參考 {self.plan_file}")
            
            return "\n\n".join(sections)

