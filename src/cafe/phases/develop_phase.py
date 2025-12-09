"""Development phase."""

import json
import readline  # Enable line editing with arrow keys
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

import yaml

from cafe.agents.manager import AgentManager
from cafe.core.git import GitOperations
from cafe.core.permission import PermissionHandler
from cafe.core.phase import Phase
from cafe.core.status_codes import PhaseStatusCode, StatusCodeParser, generate_status_code_prompt
from cafe.core.types import PhaseProgress, PhaseResult, PhaseStatus, WorkflowMode
from cafe.ui.display import Display
from cafe.utils.github import get_pr_comments, filter_unresolved_comments, format_comments_for_prompt


class DevelopPhase(Phase):
    """Phase 3: Development with developer agent."""

    def __init__(
        self,
        agent_manager: AgentManager,
        permission_handler: PermissionHandler,
        git_ops: GitOperations,
        spec_file: str,
        plan_file: str,
        workflow_mode: WorkflowMode,
        issue_id: Optional[str] = None,
        issue_name: Optional[str] = None,
        dev_agent: str = "David",
        interactive: bool = True,
        user_input: str = "",
        approved_denial_indices: Optional[List[int]] = None,
        pr_number: Optional[int] = None,
    ) -> None:
        """Initialize develop phase.

        Args:
            agent_manager: Agent manager
            permission_handler: Permission handler
            git_ops: Git operations
            spec_file: Path to spec file
            plan_file: Path to plan file
            workflow_mode: Workflow mode (local or github)
            issue_id: GitHub issue ID (required for github mode)
            issue_name: Issue name for history tracking (default: derived from current branch)
            dev_agent: Developer agent name (default: David)
            interactive: Enable interactive mode (default: True)
            user_input: User input for non-interactive mode (default: "")
            approved_denial_indices: Indices of approved permission denials (for non-interactive mode)
            pr_number: PR number to fetch unresolved comments from (optional)
        """
        super().__init__(interactive=interactive, git_ops=git_ops)

        self.agent_manager = agent_manager
        self.permission_handler = permission_handler
        self.git_ops = git_ops
        self.spec_file = spec_file
        self.plan_file = plan_file
        self.workflow_mode = workflow_mode
        self.issue_id = issue_id
        self.dev_agent = dev_agent
        self.user_input = user_input
        self.approved_denial_indices = approved_denial_indices if approved_denial_indices is not None else []
        self.pr_number = pr_number
        self._pr_comments_cache = None  # Cache for PR comments to avoid duplicate loading
        self.phase_name = "develop"  # For base class progress tracking

        # Iteration tracking
        self.iteration = 0

        # Determine issue name for history tracking (issue_dir is set by base class)
        if issue_name:
            self.issue_name = issue_name
        else:
            # Derive from current branch name (via issue_dir)
            self.issue_name = self.issue_dir.name

        # History directory for develop phase
        # Path: .cafe/issues/{issue_name}/develop/history
        self.history_dir = self.issue_dir / "develop" / "history"

        # Track user responses for permission requests
        self.user_responses: List[str] = []

        # Initialize display
        self.display = Display()

        # Restore state from last iteration file (if resuming)
        self.iteration = self._load_iteration_counter()

    def _check_plan_exists(self) -> bool:
        """Check if plan file exists (versioned or legacy plan.md).

        Returns:
            True if plan file exists, False otherwise
        """
        plan_path = Path(self.plan_file)
        if plan_path.exists():
            return True

        # Also check for versioned plan files as fallback
        plan_dir = self.issue_dir / "plan"
        latest_plan = self._get_latest_versioned_file("plan", plan_dir)
        if latest_plan and latest_plan.exists():
            # Update self.plan_file to use the latest versioned file
            self.plan_file = str(latest_plan)
            return True

        return False

    def _save_develop_clarification(self, agent_response: str) -> None:
        """儲存 developer 需要澄清的問題到 develop_{it_num}.md 檔案.

        Args:
            agent_response: Agent 回應內容（包含狀態碼）
        """
        # Remove status code line from response
        lines = agent_response.strip().split('\n')
        # Skip first line (status code) and any empty lines after it
        content_lines = []
        skip_status = True
        for line in lines:
            if skip_status and line.strip().startswith('CAFE_'):
                continue
            skip_status = False
            content_lines.append(line)

        # Remove leading empty lines
        while content_lines and not content_lines[0].strip():
            content_lines.pop(0)

        content = '\n'.join(content_lines)

        # Get file path
        develop_dir = self.issue_dir / "develop"
        develop_file = self._get_versioned_file_path("develop", self.iteration, develop_dir)

        # Ensure directory exists
        develop_file.parent.mkdir(parents=True, exist_ok=True)

        # Write content
        develop_file.write_text(content, encoding='utf-8')

    def _get_review_file_path(self) -> Path:
        """取得 review 檔案的完整路徑.

        優先返回最新的 review_XXX.md 檔案。如果沒有編號檔案，則 fallback 到 review.md（向後兼容）。

        Returns:
            review 檔案的 Path 物件
        """
        spec_path = Path(self.spec_file)
        issue_dir = spec_path.parent.parent  # .cafe/issues/{issue_name}
        review_dir = issue_dir / "review"

        # Find all review_XXX.md files
        if review_dir.exists():
            numbered_reviews = sorted(review_dir.glob("review_*.md"))
            if numbered_reviews:
                # Return the latest numbered review file
                return numbered_reviews[-1]

        # Fallback to review.md for backward compatibility
        return review_dir / "review.md"

    def _check_review_feedback_exists(self) -> bool:
        """檢查是否存在 review feedback.

        Returns:
            True if review.md exists, False otherwise
        """
        review_file = self._get_review_file_path()
        return review_file.exists()

    def _load_review_status(self) -> Optional[Dict[str, Any]]:
        """Load review phase status from status.json.

        Returns:
            Review status dict if exists, None otherwise
        """
        spec_path = Path(self.spec_file)
        issue_dir = spec_path.parent.parent
        review_status_file = issue_dir / "review" / "status.json"

        if not review_status_file.exists():
            return None

        try:
            with open(review_status_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, KeyError):
            return None


    def _save_progress_with_review_timestamp(
        self,
        status_code: PhaseStatusCode,
        handled_review_timestamp: Optional[str] = None,
    ) -> None:
        """Save phase progress to status.json with review timestamp tracking.

        Args:
            status_code: Phase status code
            handled_review_timestamp: Timestamp of review feedback that was handled (if any)
        """
        # Call base class method first
        self._save_progress(status_code)

        # Add handled_review_timestamp if provided
        if handled_review_timestamp:
            status_file = self._get_status_file()
            with open(status_file, 'r', encoding='utf-8') as f:
                progress_dict = json.load(f)
            progress_dict["handled_review_timestamp"] = handled_review_timestamp
            with open(status_file, 'w', encoding='utf-8') as f:
                json.dump(progress_dict, f, ensure_ascii=False, indent=2)


    def _save_issue_config(self, base_branch: str, feature_branch: str) -> None:
        """Save issue configuration including base branch.

        Args:
            base_branch: Base branch name (e.g., 'main')
            feature_branch: Feature branch name (e.g., 'my-feature')
        """
        config_file = self.history_dir.parent.parent / "config.yaml"

        # Read existing config to preserve worktree_path, issue_id, and rigor
        existing_config = self._read_issue_config(config_file) or {}

        # Update with base_branch and feature_branch
        config_data = {**existing_config}
        config_data["base_branch"] = base_branch
        config_data["feature_branch"] = feature_branch

        self._write_issue_config(config_file, config_data)

    def _check_if_already_completed_with_review(self) -> Optional[PhaseResult]:
        """檢查 phase 是否已完成，考慮 review feedback 和 PR comments 的特殊邏輯。

        Returns:
            PhaseResult 如果已完成且無需處理 review feedback/PR comments，None 如果需要繼續執行
        """
        existing_progress = self._load_progress()
        if not existing_progress or existing_progress.status != PhaseStatus.COMPLETED:
            return None

        # If pr_number is provided, always continue execution (user wants to address PR comments)
        if self.pr_number:
            print(f"ℹ️  PR #{self.pr_number} comments will be addressed")
            return None

        # Check if there's review feedback that requires handling
        review_status = self._load_review_status()
        if review_status and review_status.get("status_code") == "CAFE_NEEDS_CHANGES":
            # Check if this review has already been handled
            review_timestamp = review_status.get("timestamp", "")

            # Load develop status.json to check handled_review_timestamp
            status_file = self.history_dir.parent / "status.json"
            if status_file.exists():
                with open(status_file, 'r', encoding='utf-8') as f:
                    develop_status = json.load(f)
                    handled_review_timestamp = develop_status.get("handled_review_timestamp")

                    if handled_review_timestamp == review_timestamp:
                        # This review has already been handled
                        return PhaseResult(
                            status=PhaseStatus.COMPLETED,
                            message=f"Development already completed in {existing_progress.iteration} iteration(s)",
                            data={
                                "branch": self._get_branch_name(),
                                "iterations": existing_progress.iteration,
                                "status_code": existing_progress.status_code,
                            },
                        )

            # Review exists and hasn't been handled yet, continue execution
            review_file = self._get_review_file_path()
            print(f"ℹ️  Review feedback detected: {review_file}")
            return None  # Don't return early - let execution continue to handle review feedback

        # No review feedback or review passed, phase is truly completed
        return PhaseResult(
            status=PhaseStatus.COMPLETED,
            message=f"Development already completed in {existing_progress.iteration} iteration(s)",
            data={
                "branch": self._get_branch_name(),
                "iterations": existing_progress.iteration,
                "status_code": existing_progress.status_code,
            },
        )

    def _get_completion_data(self) -> dict:
        """取得 phase 完成時的額外資料（提供給 base class 的 _handle_standard_status_codes）。

        Returns:
            包含 phase-specific 資料的 dict，會被合併到 PhaseResult.data 中
        """
        data = {
            "branch": self._get_branch_name(),
        }
        return data

    def _prepare_user_input_for_iteration(self) -> "PhaseResult | str":
        """準備當前迭代的 user input。

        Develop phase 的特殊邏輯：
        - Iteration 1（沒有 previous data）: 使用 self.user_input（如果有提供 --user-input 參數）
        - Iteration 2+（有 previous data）: 檢查是否有待處理的 NEED_PERMISSION

        Returns:
            PhaseResult: 如果需要結束/暫停 phase
            str: 用戶輸入內容（通常為空，除非處理 NEED_PERMISSION 或提供了 --user-input）
        """
        # Check for previous iteration data
        prev_data = self._load_previous_iteration_data()

        # No previous data: first execution
        if not prev_data:
            # Use self.user_input if provided (from --user-input parameter)
            user_input = self.user_input if self.user_input else ""
            self.user_input = ""  # Clear after first use
            return user_input

        prev_status = prev_data.get("status_code", "")

        # Handle pending NEED_PERMISSION from previous run
        if prev_status == "CAFE_NEED_PERMISSION":
            # Check if has permission_denials
            if not prev_data.get("permission_denials"):
                # Old format without permission_denials, return empty
                return ""

            # In non-interactive mode, check if user provided approved_denial_indices
            if not self.interactive:
                if not hasattr(self, "approved_denial_indices") or not self.approved_denial_indices:
                    return PhaseResult(
                        status=PhaseStatus.FAILED,
                        message="Permission required but running in non-interactive mode without --approve-denied-tools",
                        data={
                            "iterations": prev_data['iteration'],
                            "last_response": prev_data.get('response', ''),
                            "permission_denials": prev_data.get("permission_denials", []),
                        },
                    )

            # Note: The actual permission handling (calling _handle_previous_permission_denials)
            # will be done in execute() method when constructing allowed_tools

        # Handle NEED_CLARIFICATION - use user_input if provided
        if prev_status == "CAFE_NEED_CLARIFICATION":
            user_input = self.user_input if self.user_input else ""
            self.user_input = ""  # Clear after use
            return user_input

        # No special handling needed - clear user_input to avoid misuse
        self.user_input = ""
        return ""

    def _get_last_develop_timestamp(self):
        """Get timestamp from last develop/status.json.

        Returns:
            datetime object or None if not found
        """
        try:
            status_file = self.history_dir.parent / "status.json"
            if not status_file.exists():
                return None

            with open(status_file, 'r', encoding='utf-8') as f:
                status_data = json.load(f)
                timestamp_str = status_data.get("timestamp")
                if timestamp_str:
                    from datetime import datetime
                    return datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
        except Exception:
            pass
        return None

    def _load_pr_comments(self) -> tuple[str, int]:
        """Load PR comments if pr_number is provided.

        Only loads comments that are newer than the last develop timestamp.

        Returns:
            Tuple of (formatted comments string, new unresolved count)
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

            # Filter comments that are newer than last develop timestamp
            last_develop_time = self._get_last_develop_timestamp()
            if last_develop_time:
                from datetime import datetime
                new_comments = []
                for comment in unresolved:
                    comment_time = datetime.fromisoformat(comment.created_at.replace('Z', '+00:00'))
                    if comment_time > last_develop_time:
                        new_comments.append(comment)

                print(f"  → {len(new_comments)} new unresolved comments (since last develop)")
                unresolved = new_comments

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

    def _generate_prompt(self, user_input: str = "") -> str:
        """Generate prompt for current iteration.

        Args:
            user_input: User input for this iteration (additional instructions from user)

        Returns:
            Prompt string
        """
        status_code_prompt = generate_status_code_prompt(
            valid_codes=[
                PhaseStatusCode.CONFIRMED,
                PhaseStatusCode.NEED_PERMISSION,
                PhaseStatusCode.NEED_CLARIFICATION,
            ],
            descriptions={
                PhaseStatusCode.CONFIRMED: "開發工作已完成",
                PhaseStatusCode.NEED_PERMISSION: "需要請求工具使用權限",
                PhaseStatusCode.NEED_CLARIFICATION: "需要 user 澄清業務邏輯或需求",
            },
        )

        # Load PR comments if available
        pr_comments, _ = self._load_pr_comments()
        pr_comments_section = f"\n\n{pr_comments}\n" if pr_comments else ""

        # Check for existing develop clarification file
        develop_dir = self.issue_dir / "develop"
        develop_file = self._get_latest_versioned_file("develop", develop_dir)
        develop_file_section = ""
        develop_instruction = ""
        if develop_file and develop_file.exists():
            develop_file_section = f"\n- Developer 問題記錄：{develop_file}"
            develop_instruction = f"1. **首先閱讀** {develop_file} 中的問題（如果有）\n"

        # Check if review feedback exists (every iteration, not just first)
        has_review_feedback = self._check_review_feedback_exists()
        review_file_path = self._get_review_file_path()

        config_file = self.issue_dir / "config.yaml"
        base_branch = self._get_issue_config_value(config_file, "base_branch") or "main"
        important_note = f"""
**重要**
- **嚴格與 {base_branch} 的 commit message 保持一致性**，可分多次 commit，一致性包括：
  - 語言（英文/中文）
  - message 為一行 (只有 subject line) 或多行 (subject + body)
"""

        if has_review_feedback:
            # With review feedback - 修正模式
            user_input_section = f"\n\n**用戶的額外說明：**\n{user_input}\n" if user_input else ""
            return f"""請根據 Code Review 反饋進行修正。

**你的角色：**
請使用 Read tool 讀取 agents/{self.dev_agent}.md 了解你的角色定義和工作準則，然後嚴格按照角色定義中的要求進行程式碼修正工作。

{important_note}

**檔案路徑：**
- Review Feedback：{review_file_path}
- 需求規格：{self.spec_file}
- 實作計畫：{self.plan_file}{develop_file_section}
{pr_comments_section}{user_input_section}
**執行步驟：**
1. 使用 Read tool 讀取 agents/{self.dev_agent}.md 了解角色定義
{develop_instruction}2. **首先閱讀** {review_file_path} 或 pr comments，了解所有需要修正的問題
3. 根據 review feedback 逐一修正問題
4. **嚴格按照既有的 commit message 風格撰寫 commit 訊息**，可分多次 commit
5. **禁止修改非當前分支的 commit**
6. 如果需要，可參考 {self.spec_file} 和 {self.plan_file}
7. 完成所有修正後回傳狀態碼

{status_code_prompt}

**如何請求澄清：**
遇到以下情況時，請回傳 `CAFE_NEED_CLARIFICATION` 狀態碼：
- 被要求執行的事項與角色的行為準則產生衝突

請求澄清時的步驟：
1. 回傳 `CAFE_NEED_CLARIFICATION` 狀態碼
2. 在回應中清楚描述需要澄清的問題（可使用條列式）
3. 系統會將你的問題儲存到 develop_XXX.md 檔案中
4. User 回覆後，系統會在下次執行時提供該檔案供你閱讀

**完成後回傳狀態碼就好，不要做任何總結**
"""

        # No review feedback - normal development mode
        user_input_section = f"\n\n**用戶的額外說明：**\n{user_input}\n" if user_input else ""
        return f"""請按照實作計畫執行開發工作。

**你的角色：**
請使用 Read tool 讀取 agents/{self.dev_agent}.md 了解你的角色定義和工作準則，然後嚴格按照角色定義中的要求進行開發工作。

{important_note}

**檔案路徑：**
- 需求規格：{self.spec_file}
- 實作計畫：{self.plan_file}{develop_file_section}
{pr_comments_section}{user_input_section}
**執行步驟：**
1. 使用 Read tool 讀取 agents/{self.dev_agent}.md 了解角色定義
{develop_instruction}2. 仔細閱讀 {self.spec_file} 和 {self.plan_file}
3. 嚴格按照計畫中的順序執行開發任務
4. **嚴格按照既有的 commit message 風格撰寫 commit 訊息**，可分多次 commit
5. 完成每個任務後，在 {self.plan_file} 中將該項目打勾（- [ ] 改為 - [x]）
6. **禁止修改非當前分支的 commit**
7. 所有任務完成後回傳狀態碼

{status_code_prompt}

**如何請求澄清：**
遇到以下情況時，請回傳 `CAFE_NEED_CLARIFICATION` 狀態碼：
- 被要求執行的事項與角色的行為準則產生衝突

請求澄清時的步驟：
1. 回傳 `CAFE_NEED_CLARIFICATION` 狀態碼
2. 在回應中清楚描述需要澄清的問題（可使用條列式）
3. 系統會將你的問題儲存到 develop_XXX.md 檔案中
4. User 回覆後，系統會在下次執行時提供該檔案供你閱讀

**完成後回傳狀態碼就好，不要做任何總結**
"""

    def execute(self) -> PhaseResult:
        """Execute development phase with iterative loop.

        Returns:
            Phase result
        """
        try:
            # Check plan.md exists
            if not self._check_plan_exists():
                return PhaseResult(
                    status=PhaseStatus.FAILED,
                    message=f"Plan file not found: {self.plan_file}. Please run 'cafe plan' first.",
                )

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

            # Auto-detect PR number if not provided (must happen before already_completed check)
            if not self.pr_number:
                try:
                    from cafe.utils.github import GitHubOps
                    github_ops = GitHubOps()
                    branch_name = self._get_branch_name()
                    pr_data = github_ops.get_pr_for_branch(branch_name)
                    if pr_data:
                        self.pr_number = pr_data["number"]
                        print(f"ℹ️  Auto-detected PR #{self.pr_number} for branch '{branch_name}'")
                except Exception as e:
                    # Silently ignore errors - PR detection is optional
                    print(f"ℹ️  No PR detected for current branch (this is normal if PR hasn't been created yet)")

            # Check if already completed (with review feedback awareness)
            already_completed = self._check_if_already_completed_with_review()
            if already_completed:
                return already_completed

            # Load PR comments if pr_number is available (will be included in prompt)
            # Note: We don't skip execution even if there are no new comments,
            # as the developer may still have work to do based on the plan
            if self.pr_number:
                print(f"\n🔍 Checking PR #{self.pr_number} for unresolved comments...")
                pr_comments, unresolved_count = self._load_pr_comments()
                if unresolved_count > 0:
                    print(f"✅ Found {unresolved_count} new unresolved PR comment(s) to address")
                else:
                    print(f"ℹ️  No new unresolved PR comments since last develop")
                print()

            # Create or checkout branch
            branch_name = self._get_branch_name()
            if self.git_ops.branch_exists(branch_name):
                self.git_ops.checkout_branch(branch_name)
            else:
                # Get current branch before creating new one (this is the base branch)
                base_branch = self.git_ops.get_current_branch()
                self.git_ops.create_branch(branch_name)
                # Save issue config with base branch info
                self._save_issue_config(base_branch, branch_name)

            # Load current iteration counter (will be 0 if starting fresh, or last iteration if resuming)
            self.iteration = self._load_iteration_counter()

            # Increment for next execution
            self.iteration += 1

            # Prepare user_input for this iteration
            result_or_input = self._prepare_user_input_for_iteration()
            if isinstance(result_or_input, PhaseResult):
                # Method returned a PhaseResult (completion/failure/pause)
                return result_or_input
            # Otherwise, it's the user input string
            current_user_input = result_or_input

            # Handle previous permission denials and construct allowed_tools
            base_allowed_tools = ["write", "read", "edit", "bash", "grep", "glob", "ls", "web_fetch", "web_search"]
            approved_tools_from_denials, permission_user_input = self._handle_previous_permission_denials()

            # Check if user rejected all tools (no approvals)
            prev_data = self._load_previous_iteration_data()
            if prev_data and prev_data.get("permission_denials") and not approved_tools_from_denials:
                return PhaseResult(
                    status=PhaseStatus.FAILED,
                    message="No tools approved - all permission requests were rejected.",
                    data={
                        "iterations": self.iteration,
                        "last_response": prev_data.get('response', ''),
                        "permission_denials": prev_data.get("permission_denials", []),
                    },
                )

            # Merge base tools + previous iteration's tools + newly approved tools
            allowed_tools = self._merge_allowed_tools(base_allowed_tools, approved_tools_from_denials)

            # If user provided additional input about permissions, append to current_user_input
            if permission_user_input:
                if current_user_input:
                    current_user_input = f"{current_user_input}\n\n{permission_user_input}"
                else:
                    current_user_input = permission_user_input

            # Execute full agent interaction cycle (generate prompt, execute, handle status)
            result, response = self._execute_and_handle_agent_response(
                agent_name=self.dev_agent,
                user_input=current_user_input,
                valid_status_codes=[
                    PhaseStatusCode.CONFIRMED,
                    PhaseStatusCode.NEED_PERMISSION,
                    PhaseStatusCode.NEED_CLARIFICATION,
                ],
                allowed_tools=allowed_tools,
                complete_codes=[PhaseStatusCode.CONFIRMED],
                continue_codes=[],  # No automatic continue codes
            )

            # Handle NEED_PERMISSION and NEED_CLARIFICATION specially - return and wait for next invocation
            if response:
                response_status = StatusCodeParser.extract(
                    response,
                    valid_codes=[
                        PhaseStatusCode.CONFIRMED,
                        PhaseStatusCode.NEED_PERMISSION,
                        PhaseStatusCode.NEED_CLARIFICATION,
                    ],
                )
                if response_status == PhaseStatusCode.NEED_PERMISSION:
                    # Display permission request and return IN_PROGRESS
                    if self.interactive:
                        print(f"\n{'='*60}")
                        print(f"Dev ({self.dev_agent}) - Iteration {self.iteration}:")
                        print(f"{'='*60}")
                        print(response)
                        print(f"{'='*60}\n")
                        print("💡 開發者請求權限。請再次執行 'cafe develop' 來回應。")

                        return PhaseResult(
                            status=PhaseStatus.IN_PROGRESS,
                            message=f"Permission requested in iteration {self.iteration}. Run command again to respond.",
                            data={
                                "iterations": self.iteration,
                                "last_response": response,
                                "status_code": response_status.value,
                            },
                        )
                    else:
                        return PhaseResult(
                            status=PhaseStatus.FAILED,
                            message="Permission required but running in non-interactive mode",
                            data={
                                "iterations": self.iteration,
                                "last_response": response,
                            },
                        )
                elif response_status == PhaseStatusCode.NEED_CLARIFICATION:
                    # Save clarification to file
                    self._save_develop_clarification(response)

                    # Display clarification request and return IN_PROGRESS
                    if self.interactive:
                        print(f"\n{'='*60}")
                        print(f"Dev ({self.dev_agent}) - Iteration {self.iteration}:")
                        print(f"{'='*60}")
                        print(response)
                        print(f"{'='*60}\n")
                        print("💡 開發者需要澄清。請再次執行 'cafe develop' 來回應。")

                        return PhaseResult(
                            status=PhaseStatus.IN_PROGRESS,
                            message=f"Clarification requested in iteration {self.iteration}. Run command again to respond.",
                            data={
                                "iterations": self.iteration,
                                "last_response": response,
                                "status_code": response_status.value,
                            },
                        )
                    else:
                        return PhaseResult(
                            status=PhaseStatus.COMPLETED,
                            message="Clarification needed - saved to develop file. Re-run with --user-input to provide response.",
                            data={
                                "iterations": self.iteration,
                                "last_response": response,
                                "status_code": response_status.value,
                            },
                        )

            # Phase-specific post-processing: Handle review feedback timestamp
            if result and result.status == PhaseStatus.COMPLETED:
                review_status = self._load_review_status()
                if review_status and review_status.get("status_code") == "CAFE_NEEDS_CHANGES":
                    handled_review_timestamp = review_status.get("timestamp")
                    # Update status.json with handled_review_timestamp
                    self._save_progress_with_review_timestamp(
                        PhaseStatusCode.CONFIRMED,
                        handled_review_timestamp
                    )

            if result:
                return result

            # No result means agent didn't return a status code - return IN_PROGRESS
            return PhaseResult(
                status=PhaseStatus.IN_PROGRESS,
                message=f"Iteration {self.iteration}: No status code found, need more work",
                data={"iterations": self.iteration},
            )

        except KeyboardInterrupt:
            print("\n\n⏸️  Paused by user (Ctrl+C).")
            print(f"💾 Progress saved. Current iteration: {self.iteration}")
            print(f"📝 To resume, run: cafe develop {self.issue_name}")
            return PhaseResult(
                status=PhaseStatus.IN_PROGRESS,
                message="Paused by user - can resume later",
                data={"iterations": self.iteration},
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
            # Use issue name as branch name
            return self.issue_name

    def _get_status_analysis_prompt(self) -> str:
        """取得分析 status code 的 prompt.

        Returns:
            分析 prompt 字串
        """
        return f"""請閱讀 {self.plan_file} 並檢查開發進度。

根據以下條件判斷應該回傳哪個狀態碼：

- CAFE_CONFIRMED: 所有任務都已完成（plan.md 中的項目都已打勾）
- CAFE_NEED_PERMISSION: 需要請求額外的工具使用權限

請只回傳一個狀態碼（例如：CAFE_CONFIRMED），不要有任何其他內容。"""

    def _detect_written_output_files(self) -> List[Path]:
        """檢查 develop file 是否在失敗前已寫入。

        DevelopPhase 使用 develop_{iteration}.md 來記錄 CAFE_NEED_CLARIFICATION 的問題。

        Returns:
            List[Path]: 如果 develop_{iteration}.md 存在則返回包含它的列表，否則返回空列表
        """
        develop_dir = self.issue_dir / "develop"
        develop_file = develop_dir / f"develop_{self.iteration:03d}.md"
        return [develop_file] if develop_file.exists() else []
