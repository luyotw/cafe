"""Development phase."""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from cafe.agents.manager import AgentManager
from cafe.core.git import GitOperations
from cafe.core.permission import PermissionHandler
from cafe.core.phase import Phase
from cafe.core.status_codes import PhaseStatusCode, StatusCodeParser, generate_status_code_prompt
from cafe.core.types import PhaseProgress, PhaseResult, PhaseStatus, WorkflowMode
from cafe.ui.display import Display


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
            issue_name: Issue name for history tracking (default: derived from spec_file)
            dev_agent: Developer agent name (default: David)
            interactive: Enable interactive mode (default: True)
            user_input: User input for non-interactive mode (default: "")
            approved_denial_indices: Indices of approved permission denials (for non-interactive mode)
        """
        super().__init__(interactive=interactive)

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
        self.phase_name = "develop"  # For base class progress tracking

        # Iteration tracking
        self.iteration = 0

        # Determine issue name for history tracking
        if issue_name:
            self.issue_name = issue_name
        else:
            # Derive from spec_file path: .cafe/issues/{issue_name}/spec/spec.md
            spec_path = Path(spec_file)
            self.issue_name = spec_path.parent.parent.name

        # History directory for develop phase
        # Path: .cafe/issues/{issue_name}/develop/history
        spec_path = Path(self.spec_file)
        issue_dir = spec_path.parent.parent  # .cafe/issues/{issue_name}
        self.history_dir = issue_dir / "develop" / "history"

        # Track conversation history
        self.conversation_history: List[Dict[str, Any]] = []

        # Track user responses for permission requests
        self.user_responses: List[str] = []

        # Initialize display
        self.display = Display()

        # Restore state from last iteration file (if resuming)
        self.iteration = self._load_iteration_counter()

        # Load existing conversation history if available
        self._load_conversation_history()

    def _check_plan_exists(self) -> bool:
        """Check if plan.md exists.

        Returns:
            True if plan.md exists, False otherwise
        """
        plan_path = Path(self.plan_file)
        return plan_path.exists()

    def _get_review_file_path(self) -> Path:
        """取得 review.md 的完整路徑.

        Returns:
            review.md 的 Path 物件
        """
        spec_path = Path(self.spec_file)
        issue_dir = spec_path.parent.parent  # .cafe/issues/{issue_name}
        return issue_dir / "review" / "review.md"

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

    def _load_conversation_history(self) -> None:
        """Load conversation history from previous iterations."""
        history_dir = Path(self.history_dir)
        if not history_dir.exists():
            return

        # Find all iteration files
        iteration_files = sorted(history_dir.glob("iteration_*.json"))

        for iteration_file in iteration_files:
            with open(iteration_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Restore conversation history
            self.conversation_history.append(data)

            # Restore user responses if they exist
            if "user_response" in data and data["user_response"]:
                self.user_responses.append(data["user_response"])

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
        config_file = self.history_dir.parent.parent / "config.json"
        config_file.parent.mkdir(parents=True, exist_ok=True)

        config_data = {
            "base_branch": base_branch,
            "feature_branch": feature_branch,
        }

        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(config_data, f, ensure_ascii=False, indent=2)

    def _check_if_already_completed_with_review(self) -> Optional[PhaseResult]:
        """檢查 phase 是否已完成，考慮 review feedback 的特殊邏輯。

        Returns:
            PhaseResult 如果已完成且無需處理 review feedback，None 如果需要繼續執行
        """
        existing_progress = self._load_progress()
        if not existing_progress or existing_progress.status != PhaseStatus.COMPLETED:
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
            print("ℹ️  Review feedback detected (CAFE_NEEDS_CHANGES). Continuing development...")
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
        - Iteration 1（沒有 previous data）: 空字串（agent 自主執行）
        - Iteration 2+（有 previous data）: 檢查是否有待處理的 NEED_PERMISSION

        Returns:
            PhaseResult: 如果需要結束/暫停 phase
            str: 用戶輸入內容（通常為空，除非處理 NEED_PERMISSION）
        """
        # Check for previous iteration data
        prev_data = self._load_previous_iteration_data()

        # No previous data: first execution, agent starts autonomously
        if not prev_data:
            return ""

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

        # No special handling needed
        return ""

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
            ],
            descriptions={
                PhaseStatusCode.CONFIRMED: "開發工作已完成",
                PhaseStatusCode.NEED_PERMISSION: "需要請求工具使用權限",
            },
        )

        # Check if review feedback exists (every iteration, not just first)
        has_review_feedback = self._check_review_feedback_exists()
        review_file_path = self._get_review_file_path()

        if has_review_feedback:
            # With review feedback - 修正模式
            user_input_section = f"\n\n**用戶的額外說明：**\n{user_input}\n" if user_input else ""
            return f"""請根據 Code Review 反饋進行修正。

**你的角色：**
你是一位經驗豐富的 Developer，負責根據 Code Review 的建議修正程式碼。

**重要：請先閱讀 Review Feedback**
- Review Feedback 檔案：{review_file_path}
- 請仔細閱讀 reviewer 的所有建議和問題
- 優先處理 critical 等級的問題

**檔案路徑：**
- Review Feedback：{review_file_path}
- 需求規格：{self.spec_file}
- 實作計畫：{self.plan_file}
{user_input_section}
**執行步驟：**
1. **首先閱讀** {review_file_path}，了解所有需要修正的問題
2. 根據 review feedback 逐一修正問題
3. 如果需要，可參考 {self.spec_file} 和 {self.plan_file}
4. 使用清晰的 commit message 說明修正內容
5. 完成所有修正後回傳狀態碼

{status_code_prompt}

**完成後回傳：CONFIRMED**
"""
        
        # No review feedback - normal development mode
        if self.iteration == 1:
            # First iteration: provide spec.md and plan.md paths
            user_input_section = f"\n\n**用戶的額外說明：**\n{user_input}\n" if user_input else ""
            return f"""請按照實作計畫執行開發工作。

**你的角色：**
你是一位經驗豐富的 Developer，負責根據需求規格和實作計畫進行開發。

**檔案路徑：**
- 需求規格：{self.spec_file}
- 實作計畫：{self.plan_file}
{user_input_section}
**執行步驟：**
1. 仔細閱讀 {self.spec_file} 和 {self.plan_file}
2. 嚴格按照計畫中的順序執行開發任務
3. 使用計畫中指定的 commit message（不要修改）
4. 完成每個任務後，在 {self.plan_file} 中將該項目打勾（- [ ] 改為 - [x]）
5. 所有任務完成後回傳狀態碼

{status_code_prompt}

**完成後回傳：CONFIRMED**
"""
        else:
            # Subsequent iterations: refer to history and continue
            status_code_prompt = generate_status_code_prompt(
                valid_codes=[
                    PhaseStatusCode.CONFIRMED,
                    PhaseStatusCode.NEED_PERMISSION,
                ],
                descriptions={
                    PhaseStatusCode.CONFIRMED: "開發工作已完成",
                    PhaseStatusCode.NEED_PERMISSION: "需要請求工具使用權限",
                },
            )

            # Build history reference
            history_summary = []
            for entry in self.conversation_history[-3:]:  # Last 3 iterations
                history_summary.append(f"第 {entry['iteration']} 輪：{entry['status_code']}")
            history_text = "\n".join(history_summary) if history_summary else "無歷史記錄"

            user_input_section = f"\n\n**用戶的額外說明：**\n{user_input}\n" if user_input else ""
            return f"""繼續執行開發工作。

**你的角色：**
你是一位經驗豐富的 Developer，負責根據需求規格和實作計畫進行開發。

這是第 {self.iteration} 輪開發。

**歷史記錄：**
{history_text}

**檔案路徑：**
- 需求規格：{self.spec_file}
- 實作計畫：{self.plan_file}
{user_input_section}
**執行步驟：**
1. 檢查 {self.plan_file} 了解哪些任務已完成
2. 繼續執行未完成的開發任務
3. 使用計畫中指定的 commit message（不要修改）
4. 完成每個任務後打勾
5. 所有任務完成後回傳狀態碼

{status_code_prompt}

**完成後回傳：CONFIRMED**
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

            # Check if already completed (with review feedback awareness)
            already_completed = self._check_if_already_completed_with_review()
            if already_completed:
                return already_completed

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
            base_allowed_tools = ["write", "read", "bash"]
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

            # Merge approved tools with base tools
            allowed_tools = base_allowed_tools + approved_tools_from_denials

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
                ],
                allowed_tools=allowed_tools,
                complete_codes=[PhaseStatusCode.CONFIRMED],
                continue_codes=[],  # No automatic continue codes
            )

            # Handle NEED_PERMISSION specially - return and wait for next invocation
            if response:
                response_status = StatusCodeParser.extract(
                    response,
                    valid_codes=[PhaseStatusCode.CONFIRMED, PhaseStatusCode.NEED_PERMISSION],
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
