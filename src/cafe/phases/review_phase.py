"""Code review phase."""

from pathlib import Path
from typing import List, Optional
import json
from datetime import datetime

import yaml

from cafe.agents.manager import AgentManager
from cafe.core.git import GitOperations
from cafe.core.permission import PermissionHandler
from cafe.core.phase import Phase
from cafe.core.status_codes import PhaseStatusCode, StatusCodeParser, generate_status_code_prompt
from cafe.core.types import PhaseProgress, PhaseResult, PhaseStatus, WorkflowMode
from cafe.utils.github import get_pr_comments, filter_unresolved_comments, format_comments_for_prompt


class ReviewPhase(Phase):
    """Phase 4: Code review with reviewer agent.

    Review phase is non-iterative: each execution is a single, independent code review.
    Unlike spec/plan/develop phases, there's no conversational loop.
    """

    phase_name = "review"

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
        interactive: bool = True,
        pr_number: Optional[int] = None,
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
            interactive: Enable interactive mode (default: True)
            pr_number: PR number to fetch unresolved comments from (optional)
        """
        super().__init__(interactive=interactive)

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
        self.pr_number = pr_number
        self._pr_comments_cache = None  # Cache for PR comments to avoid duplicate loading

        # Try to read base branch from issue config
        config_base_branch = self._read_base_branch_from_config()
        self.base_branch = config_base_branch if config_base_branch else base_branch

    def execute(self) -> PhaseResult:
        """Execute code review phase (single iteration).

        Review phase is non-iterative: executes once and returns result.

        Returns:
            Phase result
        """
        try:
            # Initialize history directory
            self._initialize_history_dir()

            # Check if there are any changes to review
            if not self.pr_number:  # Only check diff for non-PR reviews
                diff = self.git_ops.get_diff(
                    base=self.base_branch,
                    head=self.target_commit or "HEAD",
                )
                if not diff or not diff.strip():
                    return PhaseResult(
                        status=PhaseStatus.FAILED,
                        message="No changes to review. The diff is empty.",
                        data={
                            "target_commit": self.target_commit,
                            "base_branch": self.base_branch,
                        },
                    )

            # Check PR comments if pr_number is provided
            if self.pr_number:
                print(f"ℹ️  PR #{self.pr_number} comments will be reviewed")
                _, unresolved_count = self._load_pr_comments()
                if unresolved_count == 0:
                    return PhaseResult(
                        status=PhaseStatus.COMPLETED,
                        message=f"PR #{self.pr_number} has no unresolved comments. Nothing to review.",
                        data={
                            "pr_number": self.pr_number,
                        },
                    )

                # Check if there are commits since the latest PR comment
                if not self._has_commits_since_pr_comments():
                    return PhaseResult(
                        status=PhaseStatus.COMPLETED,
                        message=f"PR #{self.pr_number} has no new commits since latest PR comment. Nothing to review.",
                        data={
                            "pr_number": self.pr_number,
                        },
                    )

            # Calculate iteration number based on existing review files
            self.iteration = self._get_next_iteration_number()

            # Prepare allowed tools with write permission for review file
            review_file_name = f"review_{self.iteration:03d}.md"
            review_file_path = self.review_dir / review_file_name

            # Convert to project-relative path (git ignore format: / prefix)
            import os
            project_root = Path(os.getcwd())
            try:
                relative_path = review_file_path.relative_to(project_root)
                review_file_pattern = f"/{relative_path}"
            except ValueError:
                # If path is not relative to cwd, use absolute path
                review_file_pattern = str(review_file_path)

            base_allowed_tools = [
                "read",                         # Read spec and plan files
                "grep",                         # Search file content
                "glob",                         # Find files by pattern
                "ls",                           # List directory contents
                "web_fetch",                    # Fetch web content
                "web_search",                   # Search the web
                "bash(git log)",                # View commit history and messages
                "bash(git diff)",               # View code changes
                "bash(git show)",               # View specific commit details
                "bash(git status)",               # View specific commit details
                f"write({review_file_pattern})",  # Allow writing to specific review file
            ]

            # Merge base tools with previous iteration's tools (if any)
            allowed_tools = self._merge_allowed_tools(base_allowed_tools)

            # Execute review using base class method
            result, response = self._execute_and_handle_agent_response(
                agent_name=self.review_agent,
                user_input="",  # Review doesn't need user input
                valid_status_codes=[
                    PhaseStatusCode.CONFIRMED,
                    PhaseStatusCode.NEEDS_CHANGES,
                ],
                allowed_tools=allowed_tools,
                complete_codes=[PhaseStatusCode.CONFIRMED, PhaseStatusCode.NEEDS_CHANGES],
                continue_codes=[],  # No continue codes - single iteration only
            )

            # Save review to review_XXX.md file
            # Note: Real agent would write via Write tool, but we save it here to ensure
            # the file exists even in mock mode or if agent doesn't execute Write tool
            review_file_name = f"review_{self.iteration:03d}.md"
            review_file_path = self.review_dir / review_file_name
            if not review_file_path.exists():
                # Only write if agent didn't already write it via Write tool
                review_file_path.write_text(response, encoding="utf-8")

            # If base class returned a result, use it
            if result:
                return result

            # Fallback: In interactive mode, base class may return None for complete_codes
            # Extract status code from response and return completion result
            from cafe.core.status_codes import StatusCodeParser
            status_code = StatusCodeParser.extract(
                response,
                valid_codes=[
                    PhaseStatusCode.CONFIRMED,
                    PhaseStatusCode.NEEDS_CHANGES,
                ],
            )

            self._print_token_usage_summary()
            token_usage = self.agent_manager.get_total_token_usage()

            return PhaseResult(
                status=PhaseStatus.COMPLETED,
                message="Code review completed",
                data={
                    "iterations": self.iteration,
                    "final_response": response,
                    "status_code": status_code.value if status_code else None,
                    "target_commit": self.target_commit,
                    "base_branch": self.base_branch,
                    "token_usage": {
                        "input_tokens": token_usage.input_tokens,
                        "output_tokens": token_usage.output_tokens,
                        "cache_creation_input_tokens": token_usage.cache_creation_input_tokens,
                        "cache_read_input_tokens": token_usage.cache_read_input_tokens,
                        "total_cost_usd": token_usage.total_cost_usd,
                    }
                },
                token_usage=token_usage,
            )

        except Exception as e:
            import traceback
            traceback_str = traceback.format_exc()
            return PhaseResult(
                status=PhaseStatus.FAILED,
                message=f"Review phase failed: {e}\n{traceback_str}",
            )

    def _initialize_history_dir(self) -> None:
        """Initialize history directory for review."""
        # Determine review directory based on workflow mode
        if self.workflow_mode == WorkflowMode.GITHUB and self.issue_id:
            review_dir = Path(f".cafe/issues/{self.issue_id}/review")
        else:
            # Extract issue name from spec_file path and use its parent structure
            if not self.spec_file:
                raise ValueError("spec_file is required for local workflow mode")
            spec_path = Path(self.spec_file).resolve()  # Use absolute path
            # spec_file is like /path/.cafe/issues/<issue-name>/spec/spec.md
            # review_dir should be /path/.cafe/issues/<issue-name>/review
            review_dir = spec_path.parent.parent / "review"

        self.review_dir = review_dir  # Store for use in other methods
        self.history_dir = review_dir / "history"
        self.history_dir.mkdir(parents=True, exist_ok=True)

    def _get_next_iteration_number(self) -> int:
        """Get next iteration number based on existing review files.

        Returns:
            Next iteration number (1-based)
        """
        # Count existing review_*.md files in review directory
        existing_reviews = list(self.review_dir.glob("review_*.md"))
        return len(existing_reviews) + 1

    def _load_pr_comments(self) -> tuple[str, int]:
        """Load PR comments if pr_number is provided.

        Returns:
            Tuple of (formatted comments string, unresolved count)
        """
        if not self.pr_number:
            return "", 0

        # Return cached result if already loaded
        if self._pr_comments_cache is not None:
            return self._pr_comments_cache

        try:
            print(f"  → Calling get_pr_comments({self.pr_number})")
            comments = get_pr_comments(self.pr_number)
            print(f"  → Got {len(comments)} total comments")

            unresolved = filter_unresolved_comments(comments)
            print(f"  → {len(unresolved)} unresolved comments")

            result = format_comments_for_prompt(unresolved)
            if result:
                print(f"  → Formatted result length: {len(result)} chars")

            # Cache the result
            self._pr_comments_cache = (result, len(unresolved))
            return self._pr_comments_cache
        except (ValueError, Exception) as e:
            # Log error but don't fail - PR comments are optional context
            print(f"⚠️  Failed to load PR comments: {e}")
            import traceback
            traceback.print_exc()
            self._pr_comments_cache = ("", 0)
            return self._pr_comments_cache

    def _get_latest_pr_comment_timestamp(self):
        """Get timestamp of the latest PR comment.

        Returns:
            datetime object of the latest comment, or None if no comments
        """
        if not self.pr_number:
            return None

        try:
            comments = get_pr_comments(self.pr_number)
            if not comments:
                return None

            # Find the latest comment by created_at timestamp
            from datetime import datetime
            latest_timestamp = None
            for comment in comments:
                timestamp_str = comment.created_at
                if timestamp_str.endswith('Z'):
                    timestamp_str = timestamp_str.replace('Z', '+00:00')
                comment_time = datetime.fromisoformat(timestamp_str)

                if latest_timestamp is None or comment_time > latest_timestamp:
                    latest_timestamp = comment_time

            return latest_timestamp
        except Exception as e:
            print(f"⚠️  Failed to get latest PR comment timestamp: {e}")
            return None

    def _has_commits_since_pr_comments(self) -> bool:
        """Check if there are commits since the latest PR comment.

        Returns:
            True if there are new commits, False otherwise
        """
        latest_comment_time = self._get_latest_pr_comment_timestamp()
        if not latest_comment_time:
            # No PR comments, so proceed with review
            return True

        try:
            # Get commits since the latest PR comment timestamp
            timestamp_str = latest_comment_time.isoformat()
            commits = self.git_ops.get_commits_since(timestamp_str)
            return len(commits) > 0
        except Exception as e:
            print(f"⚠️  Failed to check commits since PR comments: {e}")
            # On error, assume there are new commits to be safe
            return True

    def _generate_prompt(self, user_input: str) -> str:
        """Generate review prompt (implements abstract method from Phase).

        Args:
            user_input: Not used for review phase

        Returns:
            Review prompt string
        """
        return self._generate_review_prompt()

    def _get_completion_data(self) -> dict:
        """Get phase-specific completion data (implements abstract method from Phase).

        Returns:
            Dictionary with review-specific data
        """
        return {
            "target_commit": self.target_commit,
            "base_branch": self.base_branch,
        }

    def _save_progress(self, status_code: PhaseStatusCode) -> None:
        """Save phase progress to status.json (overrides base class).

        For ReviewPhase, both CONFIRMED and NEEDS_CHANGES are completion codes.

        Args:
            status_code: Phase status code
        """
        import json
        from datetime import datetime
        from cafe.core.types import PhaseStatus, PhaseProgress

        status_file = self._get_status_file()
        status_file.parent.mkdir(parents=True, exist_ok=True)

        # Both CONFIRMED and NEEDS_CHANGES are completion statuses for review
        complete_codes = [PhaseStatusCode.CONFIRMED, PhaseStatusCode.NEEDS_CHANGES]
        phase_status = PhaseStatus.COMPLETED if status_code in complete_codes else PhaseStatus.IN_PROGRESS

        progress = PhaseProgress(
            phase=self.phase_name,
            status=phase_status,
            status_code=status_code.value,
            timestamp=datetime.now(),
            iteration=self.iteration,
            message=f"Code review completed with {status_code.value}" if phase_status == PhaseStatus.COMPLETED else f"Iteration {self.iteration}",
        )

        with open(status_file, 'w', encoding='utf-8') as f:
            json.dump(progress.to_dict(), f, ensure_ascii=False, indent=2)

    def _check_if_develop_is_newer(self) -> bool:
        """檢查 develop phase 的時間戳記是否比上次 review 更新。

        Returns:
            True 如果 develop 更新（需要重新執行所有檢查），False 否則
        """
        try:
            # 取得 issue name
            if not self.spec_file:
                return False  # No spec file, cannot compare timestamps
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

    def _generate_review_prompt(self) -> str:
        """Generate review prompt.

        Returns:
            Review prompt string
        """
        # Load PR comments if available
        pr_comments, _ = self._load_pr_comments()
        pr_comments_section = f"\n\n{pr_comments}\n" if pr_comments else ""

        # Get requirements section
        try:
            requirements_section = self._get_requirements_section()
        except Exception as e:
            raise RuntimeError(f"Error in _get_requirements_section: {e}") from e

        # 檢查是否需要重新執行檢查（develop 比 review 新）
        develop_is_newer = self._check_if_develop_is_newer()
        recheck_instruction = ""
        # 只在第 4 輪之前顯示 recheck_instruction
        if develop_is_newer and self.iteration < 4:
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

        # Add restriction for iteration 4+
        restriction = ""
        if self.iteration >= 4:
            # 上一輪的 review 檔案
            previous_review_file = f"review_{self.iteration - 1:03d}.md"
            previous_review_path = self.review_dir / previous_review_file
            restriction = f"""
⚠️ **重要限制：**
- 你現在是第 {self.iteration} 輪審查，只能針對「上一輪提出的問題」繼續追問
- 上一輪的審查內容在：{previous_review_path}
- **不可以提出新的問題**（除非是 critical 的問題，如安全性漏洞、資料損毀等）
- 只能深入釐清已經提出的問題
"""

        # Generate review file path
        review_file_name = f"review_{self.iteration:03d}.md"
        review_file_path = self.review_dir / review_file_name

        # Build prompt
        try:
            prompt = f"""你是資深軟體工程師 {self.review_agent}，正在進行第 {self.iteration} 輪程式碼審查 (Code Review)。你只會檢查當前分支有，且基礎分支 ({self.base_branch}) 沒有的 commit。

{status_code_prompt}
{recheck_instruction}
{restriction}
**審查結果儲存:**
- **必須**將完整的審查結果寫入檔案：`{review_file_path}`
- 檔案格式為 Markdown
- 內容包含所有審查發現的問題和建議

**需求規格與實作計畫:**
{requirements_section}
{pr_comments_section}

**你的審查任務（依優先順序）:**

1. **git 狀態檢查**
   - **檢查是否有未提交的變更，若有則視為開發未完成，請列出**
   - **檢查是否有機敏資訊被提交（如密碼、API key、憑證等），若有則視為 critical issue，請列出並要求立即移除，不可留在 commit 歷史中**

2. **【重要】檢查 commit message 風格一致性**
   - 比較基礎分支 ({self.base_branch}) 和當前分支的 commit message 風格是否一致
   - 若只是大小寫、標點符號等細微差異，視為一致
   - **只檢查當前分支有，且基礎分支沒有的 commit**
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

3. **仔細比對需求及實作文件**
   - 確認所有需求都已實作，且實作方式符合規劃

4. **找出潛在問題**
   - 確認是否符合專案既有 coding style
   - 檢查是否有大量重複的程式碼
   - 檢查程式碼的正確性、可讀性、效能、安全性
   - 檢查是否有不應該被提交的檔案，例如個人設定檔、log 檔案等
     - 若未 push 則要求使用 `git rebase` 或 `git filter-branch` 移除
     - 若已 push 則要求使用 `git rm --cached` 移除並更新 .gitignore 後 commit

5. **簡要說明問題**
   - 列出檔案路徑和行號並說明問題
   - 不要提供程式碼解決方案

**重要：**
- Commit message 風格問題視為 critical issue，必須修正後才能通過審查
- 審查完成後請回傳狀態碼，不要做任何總結或額外說明
"""
        except Exception as e:
            raise RuntimeError(f"Error building prompt: {e}") from e

        return prompt


    def _read_base_branch_from_config(self) -> Optional[str]:
        """Read base branch from issue config file.

        Returns:
            Base branch name if found, None otherwise
        """
        # Determine config file path based on workflow mode
        if self.workflow_mode == WorkflowMode.GITHUB and self.issue_id:
            config_file = Path(f".cafe/issues/{self.issue_id}/config.yaml")
        else:
            # Extract issue name from spec_file path
            if not self.spec_file:
                return None
            spec_path = Path(self.spec_file)
            issue_name = spec_path.parent.parent.name
            config_file = Path(f".cafe/issues/{issue_name}/config.yaml")

        config_data = self._read_issue_config(config_file)
        return config_data.get("base_branch") if config_data else None

    def _get_requirements_section(self) -> str:
        """Get requirements and plan section for review prompt.

        Returns:
            Requirements and plan section string
        """
        if self.workflow_mode == WorkflowMode.GITHUB:
            return f"請用 `gh issue view {self.issue_id}` 查看 Issue 內容（包含需求與實作分析）。"
        else:
            # 只提供檔案路徑，讓 agent 自己去讀取
            # 避免 prompt 過長
            return f"""請閱讀以下檔案來了解需求與實作計畫：
- 需求規格 (Spec): {self.spec_file}
- 實作計畫 (Plan): {self.plan_file}"""

    def _get_status_analysis_prompt(self) -> str:
        """取得分析 status code 的 prompt.

        Returns:
            分析 prompt 字串
        """
        review_file = self.review_dir / f"review_{self.iteration:03d}.md"
        return f"""請閱讀 {review_file} 並分析 Code Review 的結果。

根據以下條件判斷應該回傳哪個狀態碼：

- CAFE_CONFIRMED: 程式碼審查通過，沒有需要修正的問題
- CAFE_NEEDS_CHANGES: 有問題需要修正

請只回傳一個狀態碼（例如：CAFE_CONFIRMED），不要有任何其他內容。"""

