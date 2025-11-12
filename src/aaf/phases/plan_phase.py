"""Implementation plan phase."""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any

from aaf.agents.manager import AgentManager
from aaf.core.permission import PermissionHandler
from aaf.core.phase import Phase
from aaf.core.status_codes import PhaseStatusCode, StatusCodeParser, generate_status_code_prompt
from aaf.core.types import PhaseProgress, PhaseResult, PhaseStatus, WorkflowMode
from aaf.ui.display import Display

# Maximum number of planning iterations to prevent infinite loops
MAX_PLANNING_ITERATIONS = 10


class PlanPhase(Phase):
    """Phase 2: Implementation plan with developer agent."""

    def __init__(
        self,
        agent_manager: AgentManager,
        permission_handler: PermissionHandler,
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
            spec_file: Path to spec file
            workflow_mode: Workflow mode (local or github)
            issue_id: GitHub issue ID (required for github mode)
            issue_name: Issue name for history tracking (default: derived from spec_file)
            dev_agent: Developer agent name (default: David)
            template_path: Path to plan template file (optional)
            interactive: Whether to allow interactive prompts (default: True)
            user_input: User input for non-interactive mode (default: "")
        """
        super().__init__(interactive=interactive)
        
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

        # Determine issue name for history tracking
        if issue_name:
            self.issue_name = issue_name
        else:
            # Derive from spec_file path: .aaf/issues/{issue_name}/spec/spec.md
            spec_path = Path(spec_file)
            self.issue_name = spec_path.parent.parent.name

        # History directory for plan phase
        # Path: .aaf/issues/{issue_name}/plan/history
        spec_path = Path(self.spec_file)
        issue_dir = spec_path.parent.parent  # .aaf/issues/{issue_name}
        self.history_dir = issue_dir / "plan" / "history"

        # Load existing history if available (will create dir if needed)
        self.iteration = self._load_iteration_counter()

    def execute(self) -> PhaseResult:
        """Execute implementation plan phase.

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
                req_path = Path(self.spec_file)
                if not req_path.exists():
                    return PhaseResult(
                        status=PhaseStatus.FAILED,
                        message=f"Spec file not found: {self.spec_file}",
                    )

                # Check for plan.md with development guide section
                plan_file_path = self.history_dir.parent / "plan.md"
                if not plan_file_path.exists() or not self._has_dev_guide_section(plan_file_path):
                    if self.interactive:
                        # Prompt user to provide development guide
                        self._prompt_for_dev_guide()
                    else:
                        return PhaseResult(
                            status=PhaseStatus.FAILED,
                            message="plan.md 中缺少「開發指南」區塊",
                        )

            # Implementation plan loop
            while True:
                # Increment iteration and execute agent
                self.iteration += 1

                # Safety check: prevent infinite loops
                max_iterations_result = self._check_max_iterations(
                    MAX_PLANNING_ITERATIONS,
                    "Implementation plan"
                )
                if max_iterations_result:
                    return max_iterations_result

                # Prepare user_input for this iteration
                result_or_input = self._prepare_user_input_for_iteration()
                if isinstance(result_or_input, PhaseResult):
                    # Method returned a PhaseResult (completion/failure/pause)
                    return result_or_input
                # Otherwise, it's the user input string
                current_user_input = result_or_input

                # Execute full agent interaction cycle (generate prompt, execute, handle status)
                result, _ = self._execute_and_handle_agent_response(
                    agent_name=self.dev_agent,
                    user_input=current_user_input,
                    valid_status_codes=[
                        PhaseStatusCode.READY_FOR_REVIEW,
                        PhaseStatusCode.NEED_CLARIFICATION,
                        PhaseStatusCode.REJECTED,
                    ],
                    allowed_tools=["write", "read", "edit"],
                    complete_codes=[PhaseStatusCode.READY_FOR_REVIEW],
                    continue_codes=[PhaseStatusCode.NEED_CLARIFICATION],
                    phase_specific_data={"dev_agent": self.dev_agent},
                )
                if result:
                    return result
                # If result is None, continue to next iteration

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
        # Get paths
        plan_file_path = self.history_dir.parent / "plan.md"

        status_code_prompt = generate_status_code_prompt(
            valid_codes=[
                PhaseStatusCode.READY_FOR_REVIEW,
                PhaseStatusCode.NEED_CLARIFICATION,
                PhaseStatusCode.REJECTED,
            ],
            descriptions={
                PhaseStatusCode.READY_FOR_REVIEW: "實作分析已完成，準備好讓使用者確認",
                PhaseStatusCode.NEED_CLARIFICATION: "需要更多資訊或確認",
                PhaseStatusCode.REJECTED: "實作分析無法進行",
            },
        )

        # Add template reference if template is provided
        template_instruction = ""
        if self.template_path:
            template_instruction = f"""
**重要：必須嚴格按照模版格式撰寫**
請先閱讀 {self.template_path}，然後嚴格按照模版的格式、章節結構、撰寫風格來撰寫 plan.md。
- 嚴格使用與模版相同的章節標題和結構
- 參考模版中的內容詳細程度和撰寫風格
- 有「嚴格」、「必須」等字眼的部分，請務必保持一致
- 保持簡潔，避免過於冗長的說明
- 不要把實際的實作內容（程式碼、配置檔等）寫進文件，只寫計畫和步驟
"""

        if self.iteration == 1:
            return f"""分析 {self.spec_file} 並規劃實作步驟。

**你的角色：**
你是一位經驗豐富的 Developer，負責根據需求規格和開發指南，規劃詳細的實作步驟。
注意：你的工作是「規劃」而非「實作」，只需要寫出計畫和步驟，不要撰寫實際的程式碼或配置檔案內容。

這是第 {self.iteration} 輪實作分析。

請仔細閱讀需求文件（{self.spec_file}）和 {plan_file_path} 中的開發指南，規劃詳細的實作步驟。
{template_instruction}
{status_code_prompt}

**如果需要更多資訊（status: AAF_NEED_CLARIFICATION）：**
使用 Write tool 將以下內容寫入 {plan_file_path}：
   - 「## 開發指南」- 保留原有的開發指南內容（不要修改）
   - 「## 實作計畫」- 目前的實作分析內容
   - 「## 待確認問題」- 列出需要確認的技術問題

**如果分析完成（status: AAF_READY_FOR_REVIEW）：**
使用 Write tool 將完整實作計畫寫入 {plan_file_path}：
   - 第一部分：「## 開發指南」- 保留原有的開發指南內容（不要修改）
   - 第二部分：嚴格按照模版的章節結構和格式撰寫實作計畫
"""
        else:
            # Add user's modification request section for iteration 2+
            user_request_section = ""
            if user_input:
                user_request_section = f"""
**使用者的修改要求：**
{user_input}

"""

            return f"""繼續分析 {self.spec_file} 的最新版本。

**你的角色：**
你是一位經驗豐富的 Developer，負責根據需求規格和開發指南，規劃詳細的實作步驟。
注意：你的工作是「規劃」而非「實作」，只需要寫出計畫和步驟，不要撰寫實際的程式碼或配置檔案內容。

這是第 {self.iteration} 輪實作分析。

{user_request_section}
請先使用 Read tool 讀取 {plan_file_path} 的最新版本，然後根據使用者的修改要求，使用 Edit tool **更新**現有的實作計畫（不要全部重寫）。
{template_instruction}
{status_code_prompt}

**重要：使用 Edit tool 更新檔案**
- 使用 Edit tool 的 old_string/new_string 方式更新特定段落
- 不要使用 Write tool 重寫整個檔案
- 保留「## 開發指南」區塊不變
- 只修改需要變更的部分

**如果仍需確認（status: AAF_NEED_CLARIFICATION）：**
使用 Edit tool 更新 {plan_file_path} 中的相關段落，並在「## 待確認問題」區塊列出需要確認的技術問題。

**如果分析完成（status: AAF_READY_FOR_REVIEW）：**
使用 Edit tool 更新 {plan_file_path} 中需要修改的段落，確保實作計畫符合使用者的要求。
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
                PhaseStatusCode.REJECTED,
            ],
            descriptions={
                PhaseStatusCode.READY_FOR_REVIEW: "實作分析已完成，準備好讓使用者確認",
                PhaseStatusCode.NEED_CLARIFICATION: "需要更多資訊或確認",
                PhaseStatusCode.REJECTED: "實作分析無法進行",
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

    def _prompt_for_dev_guide(self) -> None:
        """Prompt user to write development guide when it doesn't exist."""
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
        dev_guide = self.display.get_multiline_input("請輸入開發指南").strip()

        if not dev_guide:
            raise ValueError("未提供開發指南，無法繼續")

        # Save development guide as initial plan.md
        plan_dir = self.history_dir.parent
        plan_file_path = plan_dir / "plan.md"
        plan_file_path.parent.mkdir(parents=True, exist_ok=True)
        plan_file_path.write_text(f"## 開發指南\n\n{dev_guide}\n")

        print()
        print("✅ 開發指南已記錄，開始實作規劃...")
        print()

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
        # Iteration 1: user_input is the dev guide content
        if self.iteration == 1:
            plan_file = self.history_dir.parent / "plan.md"
            return plan_file.read_text() if plan_file.exists() else ""

        # Iteration 2+: Display current plan.md content (interactive only)
        if self.interactive:
            self._display_current_plan()

        # Iteration 2+: Load previous iteration data
        prev_data = self._load_previous_iteration_data()
        if not prev_data:
            return ""

        prev_status = prev_data.get("status_code", "")

        # 根據上一輪狀態，取得用戶輸入
        if prev_status == "AAF_READY_FOR_REVIEW":
            # 需要用戶選擇：confirm/reject/modify
            if self.interactive:
                choice = self._ask_user_for_review_decision("實作計畫")
            else:
                choice = self.user_input
                if not choice:
                    # Non-interactive 但沒提供輸入 → 暫停
                    return PhaseResult(
                        status=PhaseStatus.IN_PROGRESS,
                        message=f"Plan phase paused after iteration {self.iteration - 1}, waiting for user decision on READY_FOR_REVIEW",
                        data={
                            "iterations": self.iteration - 1,
                            "last_response": prev_data.get("response", ""),
                            "status_code": "AAF_READY_FOR_REVIEW",
                        },
                    )

            # 處理用戶選擇（不再區分 interactive/non-interactive）
            return self._process_review_decision(
                choice,
                prev_data,
                "Implementation plan",
                {"dev_agent": self.dev_agent},
            )

        elif prev_status == "AAF_NEED_CLARIFICATION":
            return self._handle_need_clarification_input(prev_data, agent_display_name="開發者")
        else:
            return ""

    def _display_current_plan(self) -> None:
        """顯示目前的 plan.md 內容（僅 interactive 模式）。"""
        plan_file = self.history_dir.parent / "plan.md"
        plan_content = plan_file.read_text() if plan_file.exists() else "（檔案未產生）"

        # Get agent CLI info for display
        agent_cli = self.agent_manager.get_agent_config(self.dev_agent).cli.value

        print(f"\n{'='*60}")
        print(f"Dev ({self.dev_agent} by {agent_cli}) - 目前計畫內容 (Iteration {self.iteration - 1}):")
        print(f"{'='*60}")
        print(plan_content)
        print(f"{'='*60}\n")


