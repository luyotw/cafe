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
        """
        self.agent_manager = agent_manager
        self.permission_handler = permission_handler
        self.spec_file = spec_file
        self.workflow_mode = workflow_mode
        self.issue_id = issue_id
        self.dev_agent = dev_agent
        self.interactive = interactive
        self.template_path = template_path
        self.display = Display()
        self.iteration = 0

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

        # Track conversation history
        self.conversation_history: List[Dict[str, Any]] = []

        # Load existing history if available (will create dir if needed)
        self._load_history()

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

            # If there's existing history, show current state and get user response before continuing
            resume_with_confirmed = False
            if self.interactive and self.conversation_history and self.iteration > 0:
                print("\n" + "="*70)
                print(f"🔄 偵測到未完成的對話（已完成 {self.iteration} 輪），繼續執行...")
                print("="*70)

                plan_file = self.history_dir.parent / "plan.md"
                if plan_file.exists():
                    print(f"\n{'='*60}")
                    print(f"目前的實作計畫（第 {self.iteration} 輪後的狀態）")
                    print(f"{'='*60}")
                    print(plan_file.read_text())
                    print(f"{'='*60}\n")

                # Check the last iteration's status code
                last_status_code = self.conversation_history[-1].get("status_code") if self.conversation_history else None

                if last_status_code == PhaseStatusCode.CONFIRMED.value:
                    # Last iteration was CONFIRMED - will skip agent execution and go directly to user confirmation
                    resume_with_confirmed = True
                else:
                    # Last iteration was NEED_CLARIFICATION or other - get user's response
                    user_response = self.display.get_multiline_input("請回答開發者的問題")

                    if user_response.strip():
                        print()
                        print("✅ 已收到您的回答，正在發送給開發者處理...")
                        print()
                    else:
                        print("\n⚠️  沒有輸入內容，Phase 將終止。")
                        return PhaseResult(
                            status=PhaseStatus.FAILED,
                            message="User provided no response to clarification questions",
                            data={
                                "iterations": self.iteration,
                            },
                        )

                    # Save user response for next iteration to read
                    self._save_user_response(user_response)

            # Implementation plan loop
            while True:
                # If resuming with CONFIRMED status, skip agent execution and go directly to user confirmation
                if resume_with_confirmed:
                    # Use the last response from history (don't call agent or increment iteration)
                    response = self.conversation_history[-1].get("response", "")
                    status_code = PhaseStatusCode.CONFIRMED
                    resume_with_confirmed = False  # Only use this flag once
                else:
                    # Normal flow: increment iteration and execute agent
                    self.iteration += 1

                    # Safety check: prevent infinite loops
                    if self.iteration > MAX_PLANNING_ITERATIONS:
                        return PhaseResult(
                            status=PhaseStatus.FAILED,
                            message=f"Exceeded maximum iterations ({MAX_PLANNING_ITERATIONS}). Plan generation did not converge.",
                            data={
                                "iterations": self.iteration - 1,
                                "max_iterations": MAX_PLANNING_ITERATIONS,
                            },
                        )

                    # Prepare user_input for this iteration
                    # Iteration 1: user_input is the dev guide content
                    # Iteration 2+: user_input is the previous iteration's user_response
                    if self.iteration == 1:
                        # Read dev guide from plan.md
                        plan_file = self.history_dir.parent / "plan.md"
                        current_user_input = plan_file.read_text() if plan_file.exists() else ""
                    else:
                        # Get previous iteration's user_response
                        if self.conversation_history:
                            current_user_input = self.conversation_history[-1].get("user_response", "")
                        else:
                            current_user_input = ""

                    # Generate prompt for this iteration
                    prompt = self._generate_prompt()

                    # Execute developer agent
                    response, token_usage = self.agent_manager.execute(
                        self.dev_agent,
                        prompt,
                        allowed_tools=["write", "read"]
                    )

                    # Extract status code from response
                    status_code = StatusCodeParser.extract(
                        response,
                        valid_codes=[
                            PhaseStatusCode.CONFIRMED,
                            PhaseStatusCode.NEED_CLARIFICATION,
                            PhaseStatusCode.REJECTED,
                        ],
                    )

                    # Save history and progress after each iteration (only if status code found)
                    if status_code:
                        self._save_history(
                            user_input=current_user_input,
                            prompt=prompt,
                            response=response,
                            status_code=status_code,
                        )
                        self._save_progress(status_code)

                # Handle status codes
                if status_code == PhaseStatusCode.CONFIRMED:
                    # Save progress
                    self._save_progress(status_code)

                    if self.interactive:
                        # Interactive mode: Show plan and get user confirmation
                        plan_file = self.history_dir.parent / "plan.md"
                        plan_content = plan_file.read_text() if plan_file.exists() else "（檔案未產生）"

                        # Get agent CLI info for display
                        agent_cli = self.agent_manager.get_agent_config(self.dev_agent).cli.value

                        print(f"\n{'='*60}")
                        print(f"Dev ({self.dev_agent} by {agent_cli}) - Iteration {self.iteration}:")
                        print(f"{'='*60}")
                        print(plan_content)
                        print(f"{'='*60}\n")

                        # Ask user for confirmation
                        print("開發者認為實作計畫已完成。請確認：")
                        print("  [c] confirm - 確認計畫，繼續實作")
                        print("  [r] reject - 拒絕計畫，終止")
                        print("  [m] modify - 要求修改（輸入修改意見）")

                        while True:
                            choice = input("\n請選擇 [c/r/m]: ").strip().lower()

                            if choice == 'c':
                                # User confirms - complete the phase
                                return PhaseResult(
                                    status=PhaseStatus.COMPLETED,
                                    message=f"Implementation plan confirmed in {self.iteration} iteration(s)",
                                    data={
                                        "iterations": self.iteration,
                                        "final_response": response,
                                        "status_code": status_code.value,
                                    },
                                )
                            elif choice == 'r':
                                # User rejects - fail the phase
                                return PhaseResult(
                                    status=PhaseStatus.FAILED,
                                    message=f"Implementation plan rejected by user in iteration {self.iteration}",
                                    data={
                                        "iterations": self.iteration,
                                        "final_response": response,
                                        "status_code": "USER_REJECTED",
                                    },
                                )
                            elif choice == 'm':
                                # User wants modifications
                                modification_request = self.display.get_multiline_input("請輸入修改意見")

                                if not modification_request.strip():
                                    print("\n⚠️  沒有輸入修改意見，請重新選擇。")
                                    continue

                                print()
                                print("✅ 已收到您的修改意見，正在重新規劃...")
                                print()

                                # Save user's modification request
                                self._save_user_response(modification_request)

                                # Continue to next iteration
                                break
                            else:
                                print("❌ 無效選擇，請輸入 c, r, 或 m")

                        # If we reach here, user chose 'm' - continue loop
                        continue
                    else:
                        # Non-interactive mode: complete immediately
                        return PhaseResult(
                            status=PhaseStatus.COMPLETED,
                            message=f"Implementation plan completed in {self.iteration} iteration(s)",
                            data={
                                "iterations": self.iteration,
                                "final_response": response,
                                "status_code": status_code.value,
                            },
                        )
                elif status_code == PhaseStatusCode.REJECTED:
                    return PhaseResult(
                        status=PhaseStatus.FAILED,
                        message=f"Implementation plan rejected in iteration {self.iteration}",
                        data={
                            "iterations": self.iteration,
                            "final_response": response,
                            "status_code": status_code.value,
                        },
                    )
                elif status_code == PhaseStatusCode.NEED_CLARIFICATION:
                    # Save progress
                    self._save_progress(status_code)

                    if self.interactive:
                        # Interactive mode: Display plan content and get user input
                        # Read the plan file
                        plan_file = self.history_dir.parent / "plan.md"
                        plan_content = plan_file.read_text() if plan_file.exists() else "（檔案未產生）"

                        # Get agent CLI info for display
                        agent_cli = self.agent_manager.get_agent_config(self.dev_agent).cli.value

                        print(f"\n{'='*60}")
                        print(f"Dev ({self.dev_agent} by {agent_cli}) - Iteration {self.iteration}:")
                        print(f"{'='*60}")
                        print(plan_content)
                        print(f"{'='*60}\n")

                        # Get user's response using Display
                        user_response = self.display.get_multiline_input("請回答開發者的問題")

                        # Provide feedback after input
                        if user_response.strip():
                            print()
                            print("✅ 已收到您的回答，正在發送給開發者處理...")
                            print()

                        if not user_response.strip():
                            print("\n⚠️  沒有輸入內容，Phase 將終止。")
                            return PhaseResult(
                                status=PhaseStatus.FAILED,
                                message="User provided no response to clarification questions",
                                data={
                                    "iterations": self.iteration,
                                    "last_response": response,
                                },
                            )

                        # Save user response to file for next iteration
                        self._save_user_response(user_response)

                        # Update conversation_history so next iteration can access user_response
                        self.conversation_history.append({
                            "iteration": self.iteration,
                            "user_input": current_user_input,
                            "response": response,
                            "status_code": status_code.value,
                            "user_response": user_response,
                        })

                        # Continue to next iteration
                        # The user_response will become the user_input of next iteration
                        continue
                    else:
                        # Non-interactive mode: exit and wait for user response
                        return PhaseResult(
                            status=PhaseStatus.IN_PROGRESS,
                            message=f"Iteration {self.iteration}: 開發者需要更多資訊",
                            data={
                                "iterations": self.iteration,
                                "status_code": PhaseStatusCode.NEED_CLARIFICATION.value,
                            },
                        )
                else:
                    # No valid status code found, continue iteration
                    continue

        except Exception as e:
            return PhaseResult(
                status=PhaseStatus.FAILED,
                message=f"Plan phase failed: {e}",
            )

    def _generate_prompt(self) -> str:
        """Generate prompt for current iteration.

        Returns:
            Prompt string
        """
        if self.workflow_mode == WorkflowMode.GITHUB:
            return self._generate_github_prompt()
        else:
            return self._generate_local_prompt()

    def _generate_local_prompt(self) -> str:
        """Generate prompt for local workflow.

        Returns:
            Prompt string
        """
        # Get paths
        plan_file_path = self.history_dir.parent / "plan.md"

        status_code_prompt = generate_status_code_prompt(
            valid_codes=[
                PhaseStatusCode.CONFIRMED,
                PhaseStatusCode.NEED_CLARIFICATION,
                PhaseStatusCode.REJECTED,
            ],
            descriptions={
                PhaseStatusCode.CONFIRMED: "實作分析已完成，可以開始開發",
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

**如果需要更多資訊（status: NEED_CLARIFICATION）：**
1. 使用 Write tool 將以下內容寫入 {plan_file_path}：
   - 「## 開發指南」- 保留原有的開發指南內容（不要修改）
   - 「## 實作計畫」- 目前的實作分析內容
   - 「## 待確認問題」- 列出需要確認的技術問題
2. 寫完檔案後，只回傳：NEED_CLARIFICATION

**如果分析完成（status: CONFIRMED）：**
1. 使用 Write tool 將完整實作計畫寫入 {plan_file_path}：
   - 第一部分：「## 開發指南」- 保留原有的開發指南內容（不要修改）
   - 第二部分：嚴格按照模版的章節結構和格式撰寫實作計畫
2. 寫完檔案後，只回傳：CONFIRMED
"""
        else:
            return f"""繼續分析 {self.spec_file} 的最新版本。

**你的角色：**
你是一位經驗豐富的 Developer，負責根據需求規格和開發指南，規劃詳細的實作步驟。
注意：你的工作是「規劃」而非「實作」，只需要寫出計畫和步驟，不要撰寫實際的程式碼或配置檔案內容。

這是第 {self.iteration} 輪實作分析。

請檢查 {plan_file_path} 的最新版本，繼續完善實作計畫。
{template_instruction}
{status_code_prompt}

**如果仍需確認（status: NEED_CLARIFICATION）：**
1. 使用 Write tool 更新 {plan_file_path}：
   - 「## 開發指南」- 保留原有的開發指南內容（不要修改）
   - 「## 實作計畫」- 更新的實作分析內容
   - 「## 待確認問題」- 列出需要確認的技術問題
2. 寫完檔案後，只回傳：NEED_CLARIFICATION

**如果分析完成（status: CONFIRMED）：**
1. 使用 Write tool 將完整實作計畫寫入 {plan_file_path}：
   - 第一部分：「## 開發指南」- 保留原有的開發指南內容（不要修改）
   - 第二部分：嚴格按照模版的章節結構和格式撰寫實作計畫
2. 寫完檔案後，只回傳：CONFIRMED
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
                PhaseStatusCode.CONFIRMED: "實作分析已完成，可以開始開發",
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
            return f"""繼續分析 GitHub Issue #{self.issue_id}。

**你的角色：**
你是一位經驗豐富的 Developer，負責根據需求規格和開發指南，規劃詳細的實作步驟。

這是第 {self.iteration} 輪實作分析。

請用 `gh issue view {self.issue_id}` 檢視 Issue 的最新內容。

{status_code_prompt}

**如果需要更多資訊：**
用 `gh issue comment {self.issue_id}` 發 comment 詢問。

**如果分析完成：**
回應確認訊息。
"""


    def _save_history(
        self,
        user_input: str,
        prompt: str,
        response: str,
        status_code: PhaseStatusCode,
        agent_cli: Optional[str] = None,
        agent_session_id: Optional[str] = None,
        allowed_tools: Optional[List[str]] = None,
        denied_tools: Optional[List[str]] = None,
    ) -> None:
        """Save iteration history to JSON file.

        Each iteration = user_input (start) → agent response (end)

        Args:
            user_input: User's input at the start of this iteration (dev guide for iteration 1, user response for subsequent iterations)
            prompt: The prompt sent to agent
            response: The agent's response
            status_code: Status code from response
            agent_cli: CLI tool used by the agent (e.g., "copilot", "claude")
            agent_session_id: Session ID of the agent
            allowed_tools: List of allowed tools for the agent
            denied_tools: List of denied tools for the agent
        """
        # Use base class method with PlanPhase-specific data
        super()._save_iteration_history(
            phase_specific_data={
                "dev_agent": self.dev_agent,
                "user_input": user_input,
                "response": response,
            },
            prompt=prompt,
            agent_cli=agent_cli,
            agent_session_id=agent_session_id,
            allowed_tools=allowed_tools,
            denied_tools=denied_tools,
            status_code=status_code,
        )

        # Add to conversation history in memory (preserve old behavior)
        history_data = {
            "iteration": self.iteration,
            "timestamp": datetime.now().isoformat(),
            "dev_agent": self.dev_agent,
            "user_input": user_input,
            "prompt": prompt,
            "response": response,
            "status_code": status_code.value,
            "cli": agent_cli,
            "session_id": agent_session_id,
            "allowed_tools": allowed_tools,
            "denied_tools": denied_tools,
        }
        self.conversation_history.append(history_data)

    def _load_history(self) -> None:
        """Load existing history from JSON files."""
        if not self.history_dir.exists():
            return

        # Load all history files in order
        history_files = sorted(self.history_dir.glob("*.json"))

        for history_file in history_files:
            with open(history_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self.conversation_history.append(data)

            # Update iteration counter
            if data["iteration"] >= self.iteration:
                self.iteration = data["iteration"]

    def _save_progress(self, status_code: PhaseStatusCode) -> None:
        """Save phase progress to status.json.

        Args:
            status_code: Phase status code (CONFIRMED, NEED_CLARIFICATION, etc.)
        """
        status_file = self.history_dir.parent / "status.json"
        status_file.parent.mkdir(parents=True, exist_ok=True)

        # Determine phase status
        phase_status = PhaseStatus.COMPLETED if status_code == PhaseStatusCode.CONFIRMED else PhaseStatus.IN_PROGRESS

        progress = PhaseProgress(
            phase="plan",
            status=phase_status,
            status_code=status_code.value,
            timestamp=datetime.now(),
            iteration=self.iteration,
            message=f"Phase completed with {status_code.value}" if phase_status == PhaseStatus.COMPLETED else f"Iteration {self.iteration}",
        )

        with open(status_file, 'w', encoding='utf-8') as f:
            json.dump(progress.to_dict(), f, ensure_ascii=False, indent=2)

    def _load_progress(self) -> Optional[PhaseProgress]:
        """Load phase progress from status.json.

        Returns:
            PhaseProgress if file exists, None otherwise
        """
        status_file = self.history_dir.parent / "status.json"
        if not status_file.exists():
            return None

        with open(status_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        return PhaseProgress.from_dict(data)

    def _save_user_response(self, user_response: str) -> None:
        """Save user's response to plan file for next iteration.

        Args:
            user_response: User's response to developer's questions
        """
        plan_file = self.history_dir.parent / "plan.md"

        # Read existing content if any
        existing_content = ""
        if plan_file.exists():
            existing_content = plan_file.read_text()

        # Append user response
        updated_content = existing_content + f"\n\n---\n用戶回應（第 {self.iteration} 輪）:\n{user_response}\n"

        # Save updated content
        plan_file.parent.mkdir(parents=True, exist_ok=True)
        plan_file.write_text(updated_content)

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
