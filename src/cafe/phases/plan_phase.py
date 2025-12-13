"""Implementation plan phase."""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any

from cafe.agents.manager import AgentManager
from cafe.core.permission import PermissionHandler
from cafe.core.phase import Phase
from cafe.core.status_codes import PhaseStatusCode, StatusCodeParser, generate_status_code_prompt
from cafe.core.types import PhaseProgress, PhaseResult, PhaseStatus, WorkflowMode
from cafe.ui.display import Display
from cafe.utils.git_utils import get_repo_root

# Maximum number of planning iterations to prevent infinite loops
MAX_PLANNING_ITERATIONS = 10


class PlanPhase(Phase):
    """Phase 2: Implementation plan with developer agent."""

    def __init__(
        self,
        agent_manager: AgentManager,
        permission_handler: PermissionHandler,
        git_ops: "GitOperations",
        spec_file: str,
        workflow_mode: WorkflowMode,
        issue_id: Optional[str] = None,
        issue_name: Optional[str] = None,
        dev_agent: str = "David",
        interactive: bool = True,
        template_path: Optional[str] = None,
        user_input: str = "",
    ) -> None:
        """Initialize plan phase.

        Args:
            agent_manager: Agent manager
            permission_handler: Permission handler
            git_ops: Git operations (for deriving issue directory from current branch)
            spec_file: Path to spec file
            workflow_mode: Workflow mode (local or github)
            issue_id: GitHub issue ID (required for github mode)
            issue_name: Issue name for history tracking (default: derived from current branch)
            dev_agent: Developer agent name (default: David)
            template_path: Path to plan template file (optional)
            interactive: Whether to allow interactive prompts (default: True)
            user_input: User input for non-interactive mode (default: "")
        """
        super().__init__(interactive=interactive, git_ops=git_ops)

        self.agent_manager = agent_manager
        self.permission_handler = permission_handler
        self.spec_file = spec_file
        self.workflow_mode = workflow_mode
        self.issue_id = issue_id
        self.dev_agent = dev_agent
        self.template_path = template_path
        self.user_input = user_input
        self.display = Display()
        self.iteration = 0
        self.phase_name = "plan"  # For base class progress tracking

        # Determine issue name for history tracking (issue_dir is set by base class)
        if issue_name:
            self.issue_name = issue_name
        else:
            # Derive from current branch name (via issue_dir)
            self.issue_name = self.issue_dir.name

        # Load template from config if not explicitly provided
        self._load_plan_config()

        # Phase directory for plan phase (for versioned files)
        # Path: .cafe/issues/{issue_name}/plan
        self.phase_dir = self.issue_dir / "plan"

        # History directory for plan phase
        # Path: .cafe/issues/{issue_name}/plan/history
        self.history_dir = self.phase_dir / "history"

        # Load existing history if available (will create dir if needed)
        self.iteration = self._load_iteration_counter()

        # Initialize plan_file to None (will be set during execute)
        self.plan_file: Optional[Path] = None

    def execute(self) -> PhaseResult:
        """Execute implementation plan phase.

        Returns:
            Phase result
        """
        try:
            # Check if phase is already completed (avoid re-running completed phases)
            from cafe.core.status_codes import PhaseStatusCode
            early_exit_result = self._check_if_already_completed([
                PhaseStatusCode.CONFIRMED,
            ])
            if early_exit_result:
                return early_exit_result

            # Validate inputs
            if self.workflow_mode == WorkflowMode.GITHUB and not self.issue_id:
                return PhaseResult(
                    status=PhaseStatus.FAILED,
                    message="GitHub mode requires issue_id",
                )

            # Increment iteration and execute agent
            self.iteration += 1

            # Safety check: prevent infinite loops
            max_iterations_result = self._check_max_iterations(
                MAX_PLANNING_ITERATIONS,
                "Implementation plan"
            )
            if max_iterations_result:
                return max_iterations_result

            # Get iteration number for versioned file
            iteration_number = self._get_next_iteration_number("plan", self.phase_dir)

            # Note: Copy of previous version is deferred until just before agent execution
            # to avoid creating new iterations when interrupted before agent is called

            # Calculate versioned plan file path
            self.plan_file = self._get_versioned_file_path("plan", iteration_number, self.phase_dir)

            if self.workflow_mode == WorkflowMode.LOCAL:
                # Check requirements file exists
                req_path = Path(self.spec_file)
                if not req_path.exists():
                    return PhaseResult(
                        status=PhaseStatus.FAILED,
                        message=f"Spec file not found: {self.spec_file}",
                    )

                # Check if this is first iteration (no history files or plan files)
                has_history = self.history_dir.exists() and list(self.history_dir.glob("iteration_*.json"))
                is_first_iteration = not has_history

                # First iteration requires template
                if is_first_iteration and not self.template_path:
                    if self.interactive:
                        # Interactive mode: prompt for template selection
                        from cafe.templates.manager import TemplateManager
                        from cafe.ui.template_selector import select_template
                        from rich.console import Console
                        console = Console()

                        template_manager = TemplateManager(".cafe")
                        templates = template_manager.list_templates()

                        if not templates:
                            return PhaseResult(
                                status=PhaseStatus.FAILED,
                                message="No templates found. Use 'cafe template add <source> <name>' to add templates.",
                            )

                        console.print()
                        console.print("[yellow]First iteration requires a template.[/yellow]")
                        template_paths = {name: template_manager.get_template_path(name) for name in templates}
                        selected_template = select_template(templates, template_paths)

                        if selected_template:
                            self.template_path = str(template_manager.get_template_path(selected_template))
                            console.print(f"[dim]Using template: {selected_template}[/dim]")
                        else:
                            return PhaseResult(
                                status=PhaseStatus.FAILED,
                                message="Template selection cancelled",
                            )
                    else:
                        # Non-interactive mode: require --template option
                        return PhaseResult(
                            status=PhaseStatus.FAILED,
                            message="Template is required for first iteration. Use --template option.",
                        )

                # Only prompt for dev guide in first iteration
                # Note: self.iteration is determined by history files, not versioned plan files
                if is_first_iteration:
                    # Check for any existing plan file with development guide section
                    # Always check plan_001.md (first versioned file), not self.plan_file
                    first_plan_file = self._get_versioned_file_path("plan", 1, self.phase_dir)
                    plan_exists = first_plan_file.exists() and self._has_dev_guide_section(first_plan_file)
                else:
                    # Not first iteration: plan should already exist
                    plan_exists = True

                # First round: no plan file exists or no dev guide
                if is_first_iteration and not plan_exists:
                    # Need to get dev guide (optional)
                    dev_guide = ""
                    if self.interactive:
                        # Interactive: prompt user for development guide
                        dev_guide = self._get_dev_guide_from_user()
                    else:
                        # Non-interactive: use user_input as dev guide (can be empty)
                        dev_guide = self.user_input

                    # Save development guide as initial versioned plan file
                    self.plan_file.parent.mkdir(parents=True, exist_ok=True)
                    self.plan_file.write_text(f"## 開發指南\n\n{dev_guide}\n")

            # Prepare user_input for this iteration
            result_or_input = self._prepare_user_input_for_iteration()
            if isinstance(result_or_input, PhaseResult):
                # Method returned a PhaseResult (completion/failure/pause)
                return result_or_input
            # Otherwise, it's the user input string
            current_user_input = result_or_input

            # Prepare allowed tools with write/edit permission for versioned plan file
            # Convert to relative path (without / prefix) - 普通相對路徑
            # Use path relative to current working directory (supports worktree)
            from cafe.utils.git_utils import to_cwd_relative_path

            try:
                plan_file_pattern = to_cwd_relative_path(self.plan_file)
            except ValueError:
                # Fallback to absolute path if file is not under cwd
                plan_file_pattern = str(Path(self.plan_file).resolve())

            # Merge base tools with previous iteration's tools (if any)
            base_allowed_tools = [
                "read",
                "grep",
                "glob",
                "ls",
                "web_fetch",
                "web_search",
                f"edit({plan_file_pattern})",
            ]
            allowed_tools = self._merge_allowed_tools(base_allowed_tools)

            # Copy previous version just before calling agent (if iteration > 1)
            # This ensures we only create a new iteration when we actually execute the agent
            self._copy_previous_version("plan", iteration_number, self.phase_dir)

            # Execute full agent interaction cycle (generate prompt, execute, handle status)
            result, response = self._execute_and_handle_agent_response(
                agent_name=self.dev_agent,
                user_input=current_user_input,
                valid_status_codes=[
                    PhaseStatusCode.READY_FOR_REVIEW,
                    PhaseStatusCode.NEED_CLARIFICATION,
                ],
                allowed_tools=allowed_tools,
                complete_codes=[PhaseStatusCode.READY_FOR_REVIEW],
                continue_codes=[PhaseStatusCode.NEED_CLARIFICATION],
                phase_specific_data={"dev_agent": self.dev_agent},
            )

            if result:
                return result

            # Since we removed the while loop, if result is None (meaning need to continue),
            # we should return IN_PROGRESS with the status code from response
            from cafe.core.status_codes import StatusCodeParser
            status_code = StatusCodeParser.extract(response)

            return PhaseResult(
                status=PhaseStatus.IN_PROGRESS,
                message=f"Plan phase needs more iterations (iteration {self.iteration})",
                data={
                    "iterations": self.iteration,
                    "status_code": status_code.value if status_code else None,
                },
            )

        except Exception as e:
            return PhaseResult(
                status=PhaseStatus.FAILED,
                message=f"Plan phase failed: {e}",
            )

    def _generate_prompt(self, user_input: str) -> str:
        """Generate prompt for current iteration.

        Args:
            user_input: User's input/feedback for this iteration

        Returns:
            Prompt string
        """
        if self.workflow_mode == WorkflowMode.GITHUB:
            return self._generate_github_prompt(user_input)
        else:
            return self._generate_local_prompt(user_input)

    def _generate_local_prompt(self, user_input: str) -> str:
        """Generate prompt for local workflow.

        Args:
            user_input: User's input/feedback for this iteration

        Returns:
            Prompt string
        """
        # Use versioned plan file path (fallback to calculating it if not set)
        if self.plan_file is None:
            # For tests or edge cases where plan_file wasn't set in execute()
            iteration_number = self._get_next_iteration_number("plan", self.phase_dir)
            self.plan_file = self._get_versioned_file_path("plan", iteration_number, self.phase_dir)

        # 轉換所有檔案路徑為相對於當前工作目錄的路徑（用於 prompt）
        # 使用 to_cwd_relative_path 以支援 worktree 環境
        from cafe.utils.git_utils import to_cwd_relative_path
        
        try:
            plan_file_path = to_cwd_relative_path(Path(self.plan_file).resolve())
        except (ValueError, OSError):
            plan_file_path = self.plan_file

        try:
            spec_file_path = to_cwd_relative_path(Path(self.spec_file).resolve())
        except (ValueError, OSError):
            spec_file_path = self.spec_file

        if self.template_path:
            try:
                template_path = to_cwd_relative_path(Path(self.template_path).resolve())
            except (ValueError, OSError):
                template_path = self.template_path
        else:
            template_path = None

        status_code_prompt = generate_status_code_prompt(
            valid_codes=[
                PhaseStatusCode.READY_FOR_REVIEW,
                PhaseStatusCode.NEED_CLARIFICATION,
            ],
            descriptions={
                PhaseStatusCode.READY_FOR_REVIEW: "實作分析已完成，準備好讓使用者確認",
                PhaseStatusCode.NEED_CLARIFICATION: "需要更多資訊或確認",
            },
        )

        # Add template reference if template is provided
        template_instruction = ""
        if template_path:
            template_instruction = f"""
**重要：必須嚴格按照模版格式撰寫**
請先閱讀 {template_path}，然後嚴格按照模版的格式、章節結構、撰寫風格來撰寫 plan.md。
- 嚴格使用與模版相同的章節標題和結構
- 參考模版中的內容詳細程度和撰寫風格
- 有「嚴格」、「必須」等字眼的部分，請務必保持一致
- 保持簡潔，避免過於冗長的說明
- 不要把實際的實作內容（程式碼、配置檔等）寫進文件，只寫計畫和步驟
"""

        if self.iteration == 1:
            return f"""分析 {spec_file_path} 並規劃實作步驟。

**你的角色：**
請先使用 Read tool 讀取 .cafe/agents/developer/{self.dev_agent}.md 了解你的角色定義和工作準則，然後嚴格按照角色定義中的要求進行規劃。

這是第 {self.iteration} 輪實作分析。

**執行步驟：**
1. 使用 Read tool 讀取 .cafe/agents/developer/{self.dev_agent}.md 了解角色定義
2. 使用 Read tool 讀取 {plan_file_path} 的開發指南
3. 使用 Read tool 讀取需求文件 {spec_file_path}
4. 根據角色定義中的要求規劃實作步驟（注意：你的工作是「規劃」而非「實作」，只需要寫出計畫和步驟）
{template_instruction}
{status_code_prompt}

**重要：修改既有檔案，將實作計畫附加到檔案**
- 在「## 開發指南」區塊**之後**附加實作計畫
- 保留「## 開發指南」區塊不變

**如果需要更多資訊（status: CAFE_NEED_CLARIFICATION）：**
在開發指南之後附加：
   - 「## 實作計畫」- 目前的實作分析內容
   - 「## 待確認問題」- 列出需要確認的技術問題

**如果分析完成（status: CAFE_READY_FOR_REVIEW）：**
在開發指南之後附加完整實作計畫，嚴格按照模版的章節結構和格式撰寫。
"""
        else:
            # Add user's modification request section for iteration 2+
            user_request_section = ""
            if user_input:
                user_request_section = f"""
**使用者的修改要求：**
{user_input}

"""

            return f"""繼續分析 {spec_file_path} 的最新版本。

**你的角色：**
請使用 Read tool 讀取 .cafe/agents/developer/{self.dev_agent}.md 了解你的角色定義和工作準則，然後嚴格按照角色定義中的要求進行規劃。

這是第 {self.iteration} 輪實作分析。

{user_request_section}
**執行步驟：**
1. 使用 Read tool 讀取 .cafe/agents/developer/{self.dev_agent}.md 了解角色定義（如有必要）
2. 使用 Read tool 讀取 {plan_file_path} 的最新版本
3. 根據使用者的修改要求和角色定義，**更新**現有的實作計畫（不要全部重寫）
{template_instruction}
{status_code_prompt}

**重要：修改既有檔案，更新特定段落**
- 修改特定段落的內容（使用 old_string/new_string 方式）
- 保留「## 開發指南」區塊不變
- 只修改需要變更的部分

**如果仍需確認（status: CAFE_NEED_CLARIFICATION）：**
更新 {plan_file_path} 中的相關段落，並在「## 待確認問題」區塊列出需要確認的技術問題。

**如果分析完成（status: CAFE_READY_FOR_REVIEW）：**
更新 {plan_file_path} 中需要修改的段落，確保實作計畫符合使用者的要求。
"""

    def _generate_github_prompt(self, user_input: str) -> str:
        """Generate prompt for GitHub workflow.

        Args:
            user_input: User's input/feedback for this iteration

        Returns:
            Prompt string
        """
        status_code_prompt = generate_status_code_prompt(
            valid_codes=[
                PhaseStatusCode.READY_FOR_REVIEW,
                PhaseStatusCode.NEED_CLARIFICATION,
            ],
            descriptions={
                PhaseStatusCode.READY_FOR_REVIEW: "實作分析已完成，準備好讓使用者確認",
                PhaseStatusCode.NEED_CLARIFICATION: "需要更多資訊或確認",
            },
        )

        if self.iteration == 1:
            return f"""分析 GitHub Issue #{self.issue_id} 並規劃實作步驟。

**你的角色：**
你是一位經驗豐富的 Developer，負責根據需求規格和開發指南，規劃詳細的實作步驟。

這是第 {self.iteration} 輪實作分析。

請用 `gh issue view {self.issue_id}` 讀取 Issue 內容，根據需求和開發指南規劃詳細的實作步驟。

{status_code_prompt}

**如果需要更多資訊：**
用 `gh issue comment {self.issue_id}` 發 comment 詢問。

**如果分析完成：**
回應確認訊息。
"""
        else:
            # Add user's modification request section for iteration 2+
            user_request_section = ""
            if user_input:
                user_request_section = f"""
**使用者的修改要求：**
{user_input}

"""

            return f"""繼續分析 GitHub Issue #{self.issue_id}。

**你的角色：**
你是一位經驗豐富的 Developer，負責根據需求規格和開發指南，規劃詳細的實作步驟。

這是第 {self.iteration} 輪實作分析。

{user_request_section}請用 `gh issue view {self.issue_id}` 檢視 Issue 的最新內容。

{status_code_prompt}

**如果需要更多資訊：**
用 `gh issue comment {self.issue_id}` 發 comment 詢問。

**如果分析完成：**
回應確認訊息。
"""


    def _has_dev_guide_section(self, plan_file: Path) -> bool:
        """Check if plan.md has development guide section.

        Args:
            plan_file: Path to plan.md

        Returns:
            True if development guide section exists
        """
        if not plan_file.exists():
            return False

        content = plan_file.read_text()
        # Check for development guide heading
        patterns = [
            r"##\s*開發指南",
            r"##\s*[Dd]evelopment\s+[Gg]uide",
        ]

        for pattern in patterns:
            if re.search(pattern, content, re.IGNORECASE):
                return True

        return False

    def _get_dev_guide_from_user(self) -> str:
        """Prompt user to provide development guide.

        Returns:
            Development guide content from user input
        """
        print("\n" + "="*70)
        print("請提供開發指南，說明實作方向與技術背景資訊：")
        print("="*70)
        print()
        print("開發指南應該包含：")
        print("  1. 建議的技術方案或實作方向")
        print("  2. 相關的程式碼位置或模組說明")
        print("  3. 需要注意的技術限制或依賴關係")
        print("  4. 其他開發者可能不知道的背景資訊")
        print()
        print("範例：")
        print("  這個功能應該在 src/core/processor.py 中實作，")
        print("  可以參考現有的 DataProcessor 類別。")
        print("  注意要保持與現有 API 的向後相容性。")
        print()

        # Get development guide using Display for better Unicode support
        dev_guide = self.display.get_multiline_input("請輸入開發指南（可留空）").strip()

        if dev_guide:
            print()
            print("✅ 開發指南已記錄，開始實作規劃...")
        else:
            print()
            print("ℹ️  未提供開發指南，將直接開始實作規劃...")
        print()

        return dev_guide

    def _prepare_user_input_for_iteration(self) -> "PhaseResult | str":
        """準備當前迭代的 user input。

        在最開始就取得所有需要的用戶輸入：
        - Interactive: 從 stdin 詢問用戶
        - Non-interactive: 從 self.user_input 取得

        之後的處理邏輯完全相同，不再區分 interactive/non-interactive。

        Returns:
            PhaseResult: 如果需要結束/暫停 phase（完成、失敗、或等待用戶輸入）
            str: 用戶輸入內容（供 agent 使用）
        """
        # Iteration 1: user_input is the dev guide content from versioned plan file
        if self.iteration == 1:
            # Always read from plan_001.md (the first versioned file)
            # Note: Iteration number is determined by history files, not by versioned plan files
            first_plan_file = self._get_versioned_file_path("plan", 1, self.phase_dir)
            return first_plan_file.read_text() if first_plan_file.exists() else ""

        # Iteration 2+: Check if current iteration was interrupted (has user_input but no response)
        current_data = self._load_current_iteration_data()
        if current_data and current_data.get("user_input") and not current_data.get("response"):
            # 恢復被中斷的 iteration，直接使用已儲存的 user_input
            return current_data["user_input"]

        # Iteration 2+: Display current plan.md content (interactive only)
        if self.interactive:
            self._display_current_plan()

        # Iteration 2+: Load previous iteration data
        prev_data = self._load_previous_iteration_data()
        if not prev_data:
            return ""

        prev_status = prev_data.get("status_code", "")

        # 根據上一輪狀態，取得用戶輸入
        if prev_status == "CAFE_READY_FOR_REVIEW":
            # 需要用戶選擇：confirm/modify
            if self.interactive:
                choice = self._ask_user_for_review_decision("實作計畫")
            else:
                choice = self.user_input
                # Non-interactive 模式：用完後清空，確保不重複使用
                self.user_input = ""
                
                if not choice:
                    # Non-interactive 但沒提供輸入 → 立即失敗
                    return PhaseResult(
                        status=PhaseStatus.FAILED,
                        message=f"Plan phase failed after iteration {self.iteration - 1}: received READY_FOR_REVIEW in non-interactive mode without user input",
                        data={
                            "iterations": self.iteration - 1,
                            "last_response": prev_data.get("response", ""),
                            "status_code": "CAFE_READY_FOR_REVIEW",
                        },
                    )

            # 處理用戶選擇（不再區分 interactive/non-interactive）
            return self._process_review_decision(
                choice,
                prev_data,
                "Implementation plan",
                {"dev_agent": self.dev_agent},
            )

        elif prev_status == "CAFE_NEED_CLARIFICATION":
            return self._handle_need_clarification_input(prev_data, agent_display_name="開發者")
        else:
            return ""

    def _display_current_plan(self) -> None:
        """顯示目前的 plan 檔案內容（僅 interactive 模式）。"""
        # Display the previous version (current iteration - 1)
        prev_iteration = self.iteration - 1
        if prev_iteration > 0:
            prev_plan_file = self._get_versioned_file_path("plan", prev_iteration, self.phase_dir)
            print(f"\n💾 載入最新的需求規格檔案： {prev_plan_file}\n")
            plan_content = prev_plan_file.read_text() if prev_plan_file.exists() else "（檔案未產生）"
        else:
            plan_content = "（檔案未產生）"

        # Get agent CLI info for display
        agent_cli = self.agent_manager.get_agent_config(self.dev_agent).cli.value

        print(f"\n{'='*60}")
        print(f"Dev ({self.dev_agent} by {agent_cli}) - 目前計畫內容 (Iteration {self.iteration - 1}):")
        print(f"{'='*60}")
        print(plan_content)
        print(f"{'='*60}\n")

    def _get_status_analysis_prompt(self) -> str:
        """取得分析 status code 的 prompt.

        Returns:
            分析 prompt 字串
        """
        return f"""請閱讀 {self.plan_file} 並分析目前的狀態。

根據以下條件判斷應該回傳哪個狀態碼：

- CAFE_READY_FOR_REVIEW: 實作計畫已完成，可以給用戶審核
- CAFE_NEED_CLARIFICATION: 還有問題需要與用戶確認

請只回傳一個狀態碼（例如：CAFE_READY_FOR_REVIEW），不要有任何其他內容。"""

    def _detect_written_output_files(self) -> List[Path]:
        """檢查 plan file 是否在失敗前已寫入。

        Returns:
            List[Path]: 如果 plan_{iteration}.md 存在則返回包含它的列表，否則返回空列表
        """
        plan_file = self._get_versioned_file_path("plan", self.iteration, self.phase_dir)
        return [Path(plan_file)] if Path(plan_file).exists() else []

    def _load_plan_config(self) -> None:
        """Load plan configuration (template) from config.yaml if exists."""
        # If template_path is already explicitly provided, don't override it
        if self.template_path:
            return

        # Path: .cafe/issues/{issue_name}/config.yaml
        config_file = self.issue_dir / "config.yaml"

        config_data = self._read_issue_config(config_file)
        if config_data:
            # Load from plan section if exists
            plan_config = config_data.get("plan", {})

            if "template" in plan_config:
                # Resolve template name to path
                from cafe.templates.manager import TemplateManager
                template_manager = TemplateManager(".cafe")
                template_path = template_manager.get_template_path(plan_config["template"])
                if template_path and template_path.exists():
                    self.template_path = str(template_path)


