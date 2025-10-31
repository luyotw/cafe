"""Specification phase (requirements clarification)."""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from aaf.agents.manager import AgentManager
from aaf.core.permission import PermissionHandler
from aaf.core.phase import Phase
from aaf.core.status_codes import PhaseStatusCode, StatusCodeParser, generate_status_code_prompt
from aaf.core.types import PhaseProgress, PhaseResult, PhaseStatus, WorkflowMode
from aaf.ui.display import Display


def create_github_issue(content: str) -> str:
    """Create a new GitHub issue with content.

    Args:
        content: Issue content

    Returns:
        Issue ID

    Note:
        This is a placeholder. Actual implementation should use gh CLI.
    """
    # TODO: Implement using gh CLI
    # gh issue create --title "..." --body "..."
    raise NotImplementedError("GitHub issue creation not yet implemented")


def update_github_issue(issue_id: str, content: str) -> None:
    """Update existing GitHub issue.

    Args:
        issue_id: Issue ID
        content: Updated content

    Note:
        This is a placeholder. Actual implementation should use gh CLI.
    """
    # TODO: Implement using gh CLI
    # gh issue edit <issue_id> --body "..."
    raise NotImplementedError("GitHub issue update not yet implemented")


class SpecPhase(Phase):
    """Specification phase: Requirements clarification with PM agent."""

    def __init__(
        self,
        agent_manager: AgentManager,
        permission_handler: PermissionHandler,
        spec_file: str,
        workflow_mode: WorkflowMode,
        issue_id: Optional[str] = None,
        pm_agent: str = "Roger",
        interactive: bool = True,
        issue_name: Optional[str] = None,
        rigor: Optional["SpecRigor"] = None,
    ) -> None:
        """Initialize requirements phase.

        Args:
            agent_manager: Agent manager
            permission_handler: Permission handler
            spec_file: Path to spec file
            workflow_mode: Workflow mode (local or github)
            issue_id: GitHub issue ID (required for github mode)
            pm_agent: PM agent name (default: Roger)
            interactive: Enable interactive mode for user input (default: True)
            issue_name: Issue name for history tracking (default: derived from spec_file)
            rigor: Specification rigor level (default: medium)
        """
        from aaf.core.types import SpecRigor

        self.agent_manager = agent_manager
        self.permission_handler = permission_handler
        self.spec_file = spec_file
        self.workflow_mode = workflow_mode
        self.issue_id = issue_id
        self.pm_agent = pm_agent
        self.interactive = interactive

        # Track if rigor was explicitly set (for interactive prompting)
        if rigor is not None:
            self.rigor = rigor
            self._rigor_explicitly_set = True
        else:
            self.rigor = SpecRigor.MEDIUM  # Default, will prompt if interactive
            self._rigor_explicitly_set = False

        self.iteration = 0

        # Determine issue name for history tracking
        if issue_name:
            self.issue_name = issue_name
        else:
            # Derive from spec_file path: .aaf/issues/{issue_name}/spec/spec.md
            spec_path = Path(spec_file)
            self.issue_name = spec_path.parent.parent.name

        # History directory for spec phase
        # Path: .aaf/issues/{issue_name}/spec/history
        spec_path = Path(self.spec_file)
        issue_dir = spec_path.parent.parent  # .aaf/issues/{issue_name}
        self.history_dir = issue_dir / "spec" / "history"

        # Track conversation history
        self.conversation_history = []
        self.confirmed_requirements = []
        self.pending_questions = []

        # Initialize display for better input handling
        self.display = Display()

        # Load existing history if available
        self._load_history()

    def execute(self) -> PhaseResult:
        """Execute requirements clarification phase.

        Returns:
            Phase result
        """
        try:
            # Ask for rigor level if interactive and not set
            if self.interactive and self.iteration == 0:
                self._prompt_for_rigor()

            # Check if already confirmed - skip execution
            status_file = self._get_status_file()
            if status_file.exists():
                with open(status_file, "r", encoding="utf-8") as f:
                    status_data = json.load(f)
                if status_data.get("status") == "completed" and status_data.get("status_code") == "CONFIRMED":
                    # Already confirmed, return completed result
                    token_usage = self.agent_manager.get_total_token_usage()
                    return PhaseResult(
                        status=PhaseStatus.COMPLETED,
                        message="Spec already confirmed",
                        data={
                            "iterations": status_data.get("iteration", self.iteration),
                            "status_code": PhaseStatusCode.CONFIRMED.value,
                            "token_usage": {
                                "input_tokens": token_usage.input_tokens,
                                "output_tokens": token_usage.output_tokens,
                                "cache_creation_input_tokens": token_usage.cache_creation_input_tokens,
                                "cache_read_input_tokens": token_usage.cache_read_input_tokens,
                                "total_cost_usd": token_usage.total_cost_usd,
                            },
                        },
                    )

            # Validate inputs
            # Note: GitHub mode can now work without issue_id (will create new issue)

            if self.workflow_mode == WorkflowMode.LOCAL:
                # Check if spec file exists
                spec_path = Path(self.spec_file)
                if spec_path.exists():
                    # Backup original spec if exists
                    self._backup_spec(spec_path)
                elif not self.conversation_history:
                    # File doesn't exist AND no history - get initial user story
                    if self.interactive:
                        self._prompt_for_user_story()
                    else:
                        # Non-interactive mode: read user story from stdin
                        import sys
                        user_story = sys.stdin.read().strip()

                        # Remove END marker if present
                        if user_story.upper().endswith("END"):
                            lines = user_story.split('\n')
                            if lines[-1].strip().upper() == "END":
                                user_story = '\n'.join(lines[:-1]).strip()

                        if not user_story:
                            return PhaseResult(
                                status=PhaseStatus.FAILED,
                                message="No user story provided in non-interactive mode",
                                data={"iterations": 0},
                            )

                        # Create spec file with user story
                        spec_path.write_text(user_story, encoding="utf-8")

            # If there's existing history, show current state and get user response before continuing
            if self.interactive and self.conversation_history and self.iteration > 0:
                print("\n" + "="*70)
                print(f"🔄 偵測到未完成的對話（已完成 {self.iteration} 輪），繼續執行...")
                print("="*70)

                spec_file = Path(self.spec_file)
                if spec_file.exists():
                    print(f"\n{'='*60}")
                    print(f"目前的需求規格（第 {self.iteration} 輪後的狀態）")
                    print(f"{'='*60}")
                    print(spec_file.read_text())
                    print(f"{'='*60}\n")

                # Get user's response to continue the conversation
                user_response = self.display.get_multiline_input("請回答 PM 的問題")

                if user_response.strip():
                    print()
                    print("✅ 已收到您的回答，正在發送給 PM 處理...")
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

                # Update conversation history in memory with new user response
                # Note: We append a new conversation entry for the next iteration to process
                # Mark it as from_resume so we don't ask for user input again after PM responds
                self.conversation_history.append({
                    "iteration": self.iteration + 1,
                    "pm_response": "",  # Will be filled by next iteration
                    "user_response": user_response,
                    "from_resume": True,  # Flag to indicate this user response came from resume
                })

                # Update context file so next iteration can see the user response
                self._update_context_file()

            # Requirements clarification loop
            while True:
                self.iteration += 1

                # Check if current iteration's user response came from resume
                is_from_resume = False
                if self.iteration <= len(self.conversation_history):
                    current_conv = self.conversation_history[self.iteration - 1]
                    is_from_resume = current_conv.get("from_resume", False)

                # In non-interactive mode after iteration 1, read user response from stdin
                if not self.interactive and self.iteration > 1:
                    import sys
                    user_response = sys.stdin.read().strip()

                    # Remove END marker if present
                    if user_response.upper().endswith("END"):
                        lines = user_response.split('\n')
                        if lines[-1].strip().upper() == "END":
                            user_response = '\n'.join(lines[:-1]).strip()

                    if not user_response:
                        return PhaseResult(
                            status=PhaseStatus.FAILED,
                            message=f"No user response provided for iteration {self.iteration}",
                            data={
                                "iterations": self.iteration - 1,
                                "last_iteration": self.iteration - 1,
                            },
                        )

                    # Save user response to spec file
                    self._save_user_response(user_response)

                    # Save to conversation history
                    if self.conversation_history:
                        # Update the last iteration's user response
                        self.conversation_history[-1]["user_response"] = user_response

                        # Save iteration history for previous iteration with user response
                        self._save_iteration_history(
                            pm_response=self.conversation_history[-1]["pm_response"],
                            user_response=user_response,
                            status=PhaseStatusCode.NEED_CLARIFICATION,
                        )

                # Generate prompt for this iteration
                prompt = self._generate_prompt()

                # Execute PM agent with Write tool access for writing spec.md
                response = self.agent_manager.execute(
                    self.pm_agent,
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

                # Handle status codes
                if status_code == PhaseStatusCode.CONFIRMED:
                    # Save generated requirements
                    self._save_spec(response)

                    # Save final iteration history
                    self._save_iteration_history(
                        pm_response=response,
                        user_response="",
                        status=PhaseStatusCode.CONFIRMED,
                    )

                    # Save progress to status.json
                    self._save_progress(status_code)

                    # Get token usage statistics
                    token_usage = self.agent_manager.get_total_token_usage()

                    # Print token usage summary
                    print()
                    print("=" * 60)
                    print("📊 Token Usage Summary")
                    print("=" * 60)
                    print(f"Input tokens:              {token_usage.input_tokens:,}")
                    print(f"Output tokens:             {token_usage.output_tokens:,}")
                    print(f"Cache creation tokens:     {token_usage.cache_creation_input_tokens:,}")
                    print(f"Cache read tokens:         {token_usage.cache_read_input_tokens:,}")
                    print(f"Total cost:                ${token_usage.total_cost_usd:.4f}")
                    print("=" * 60)
                    print()

                    result_data = {
                        "iterations": self.iteration,
                        "final_response": response,
                        "status_code": status_code.value,
                        "token_usage": {
                            "input_tokens": token_usage.input_tokens,
                            "output_tokens": token_usage.output_tokens,
                            "cache_creation_input_tokens": token_usage.cache_creation_input_tokens,
                            "cache_read_input_tokens": token_usage.cache_read_input_tokens,
                            "total_cost_usd": token_usage.total_cost_usd,
                        }
                    }

                    # Add issue_id if GitHub mode and created new issue
                    if self.workflow_mode == WorkflowMode.GITHUB and hasattr(self, '_created_issue_id'):
                        result_data["issue_id"] = self._created_issue_id

                    return PhaseResult(
                        status=PhaseStatus.COMPLETED,
                        message=f"Requirements clarified in {self.iteration} iteration(s)",
                        data=result_data,
                        token_usage=token_usage,
                    )
                elif status_code == PhaseStatusCode.REJECTED:
                    return PhaseResult(
                        status=PhaseStatus.FAILED,
                        message=f"Requirements rejected in iteration {self.iteration}",
                        data={
                            "iterations": self.iteration,
                            "final_response": response,
                            "status_code": status_code.value,
                        },
                    )
                elif status_code == PhaseStatusCode.NEED_CLARIFICATION:
                    # Copy spec from history and sync to target (local file or GitHub issue)
                    self._save_spec(response)

                    # Save progress to status.json
                    self._save_progress(status_code)

                    # If this iteration's user response came from resume, we already have it
                    # Save the iteration and continue loop (don't ask user again for this iteration)
                    if is_from_resume:
                        # Get user response from conversation history
                        user_response_from_resume = self.conversation_history[self.iteration - 1]["user_response"]

                        # Save iteration history
                        self._save_iteration_history(
                            pm_response=response,
                            user_response=user_response_from_resume,
                            status=PhaseStatusCode.NEED_CLARIFICATION,
                        )

                        # Update the conversation entry with PM's response
                        self.conversation_history[self.iteration - 1]["pm_response"] = response
                        # Clear the from_resume flag so next iteration asks for user input normally
                        self.conversation_history[self.iteration - 1]["from_resume"] = False

                        # Continue to next iteration without asking user input
                        continue

                    if self.interactive:
                        # Interactive mode: Display PM's questions and get user input
                        # Read the spec file
                        spec_file = Path(self.spec_file)
                        pm_content = spec_file.read_text() if spec_file.exists() else "（檔案未產生）"

                        # Get CLI info for display
                        pm_cli = self.agent_manager.get_agent_config(self.pm_agent).cli.value

                        print(f"\n{'='*60}")
                        print(f"PM ({self.pm_agent} by {pm_cli}) - Iteration {self.iteration}:")
                        print(f"{'='*60}")
                        print(pm_content)
                        print(f"{'='*60}\n")

                        # Get user's response using Display for better Unicode support
                        user_response = self.display.get_multiline_input("請回答 PM 的問題")

                        # Provide feedback after END input
                        if user_response.strip():
                            print()
                            print("✅ 已收到您的回答，正在發送給 PM 處理...")
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

                        # Save iteration history
                        self._save_iteration_history(
                            pm_response=response,
                            user_response=user_response,
                            status=PhaseStatusCode.NEED_CLARIFICATION,
                        )

                        # Update conversation history
                        self.conversation_history.append({
                            "iteration": self.iteration,
                            "pm_response": response,
                            "user_response": user_response,
                            "status": "NEED_CLARIFICATION",
                        })
                    else:
                        # Non-interactive mode: save PM's questions and exit
                        # User response will be provided in next call via stdin
                        self._save_iteration_history(
                            pm_response=response,
                            user_response="",  # Will be filled in next call
                            status=PhaseStatusCode.NEED_CLARIFICATION,
                        )

                        # Add to conversation history (without user response for now)
                        self.conversation_history.append({
                            "iteration": self.iteration,
                            "pm_response": response,
                            "user_response": "",  # Will be filled in next call
                            "status": "NEED_CLARIFICATION",
                        })

                        # Exit and wait for next call with user response
                        return PhaseResult(
                            status=PhaseStatus.IN_PROGRESS,
                            message=f"Iteration {self.iteration}: PM asked clarification questions",
                            data={
                                "iterations": self.iteration,
                                "status_code": PhaseStatusCode.NEED_CLARIFICATION.value,
                            },
                        )

                    # Continue to next iteration (interactive mode only)
                    continue
                else:
                    # No valid status code found, continue iteration
                    continue

        except KeyboardInterrupt:
            # User paused with Ctrl+C - save progress and allow resume
            print("\n\n⏸️  Paused by user (Ctrl+C).")
            print(f"💾 Progress saved. Current iteration: {self.iteration}")
            print(f"📝 To resume, run: aaf spec {self.issue_name}")
            return PhaseResult(
                status=PhaseStatus.IN_PROGRESS,
                message="Paused by user - can resume later",
                data={"iterations": self.iteration},
            )
        except Exception as e:
            return PhaseResult(
                status=PhaseStatus.FAILED,
                message=f"Requirements phase failed: {e}",
            )

    def _prompt_for_rigor(self) -> None:
        """Prompt user to select rigor level if not already set."""
        from aaf.core.types import SpecRigor

        # Check if rigor is already explicitly set (not default)
        # We only prompt if it's still at default value
        if self._rigor_explicitly_set:
            return

        print("\n" + "="*70)
        print("請選擇規格嚴謹程度：")
        print("="*70)
        print()
        print("1. Low (低) - 快速開發模式")
        print("   • 只問最關鍵的資訊")
        print("   • 允許模糊地帶，讓開發者自行判斷")
        print("   • 適合：快速原型、MVP、內部工具")
        print()
        print("2. Medium (中) - 平衡模式 [預設]")
        print("   • 詢問重要細節和關鍵場景")
        print("   • 在速度和精確度間取得平衡")
        print("   • 適合：一般功能開發")
        print()
        print("3. High (高) - 精確規格模式")
        print("   • 詳細詢問所有細節和邊界情況")
        print("   • 確保需求可測試、無模糊")
        print("   • 適合：核心功能、API 設計、對外產品")
        print()
        print("="*70)

        while True:
            choice = input("請選擇 (1-3, 直接按 Enter 使用預設值 2): ").strip()

            if choice == "" or choice == "2":
                self.rigor = SpecRigor.MEDIUM
                print(f"✓ 已選擇：Medium (中) - 平衡模式\n")
                break
            elif choice == "1":
                self.rigor = SpecRigor.LOW
                print(f"✓ 已選擇：Low (低) - 快速開發模式\n")
                break
            elif choice == "3":
                self.rigor = SpecRigor.HIGH
                print(f"✓ 已選擇：High (高) - 精確規格模式\n")
                break
            else:
                print("❌ 無效選擇，請輸入 1, 2, 或 3")

    def _prompt_for_user_story(self) -> None:
        """Prompt user to write initial user story when no requirements file exists."""
        print("\n" + "="*70)
        print("請用使用者故事格式描述你的需求（可以寫多個）：")
        print("="*70)
        print()
        print("格式：身為[角色]，我想要[功能]，以便[目的/價值]")
        print()
        print("範例 1（非技術需求）:")
        print("  身為產品經理，我想要快速了解專案進度，以便向團隊報告開發狀態")
        print()
        print("範例 2（非技術需求）:")
        print("  身為用戶，我想要看到清楚的錯誤訊息，以便知道哪裡出問題並如何修正")
        print()
        print("範例 3（技術需求）:")
        print("  身為開發者，我想要在 CLI 中看到彩色的權限請求提示，以便快速識別哪些操作需要我確認")
        print()
        print("="*70)
        print("請輸入你的使用者故事（可多行，單獨一行輸入 END 表示結束）:")
        print()

        # Get user's story using Display for better Unicode support
        user_story = self.display.get_multiline_input("請輸入你的使用者故事").strip()

        if not user_story:
            raise ValueError("未提供使用者故事，無法繼續")

        # Save user story as initial spec
        spec_path = Path(self.spec_file)
        spec_path.parent.mkdir(parents=True, exist_ok=True)
        spec_path.write_text(f"# 初始需求\n\n{user_story}\n")

        print()
        print("✅ 使用者故事已記錄，開始需求澄清...")
        print()

    def _backup_spec(self, spec_path: Path) -> None:
        """Backup original spec file.

        Args:
            spec_path: Path to spec file
        """
        backup_path = Path(f"{spec_path}.backup")
        if not backup_path.exists():
            backup_path.write_text(spec_path.read_text())

    def _save_spec(self, response: str) -> None:
        """Sync spec to GitHub if needed.

        PM agent writes directly to spec_file. This method:
        - For local mode: no action needed (file already written)
        - For GitHub mode: syncs spec_file content to issue

        Args:
            response: Agent response (not used, agent already wrote file)
        """
        spec_path = Path(self.spec_file)
        if not spec_path.exists():
            return  # PM hasn't written the file yet

        if self.workflow_mode == WorkflowMode.GITHUB:
            # Sync to GitHub issue
            content = spec_path.read_text()
            if not self.issue_id and not hasattr(self, '_created_issue_id'):
                # Create new issue (only once)
                self._created_issue_id = create_github_issue(content)
            elif hasattr(self, '_created_issue_id'):
                # Update the created issue
                update_github_issue(self._created_issue_id, content)
            else:
                # Update existing issue
                update_github_issue(self.issue_id, content)

    def _save_user_response(self, user_response: str) -> None:
        """Save user's response to spec file for next iteration.

        Args:
            user_response: User's response to PM's questions
        """
        if self.workflow_mode == WorkflowMode.LOCAL:
            spec_path = Path(self.spec_file)

            # Read existing content if any
            existing_content = ""
            if spec_path.exists():
                existing_content = spec_path.read_text()

            # Append user response
            updated_content = existing_content + f"\n\n---\n用戶回應（第 {self.iteration} 輪）:\n{user_response}\n"

            # Save updated content
            spec_path.parent.mkdir(parents=True, exist_ok=True)
            spec_path.write_text(updated_content)
        elif self.workflow_mode == WorkflowMode.GITHUB:
            # For GitHub mode, add comment to issue
            # TODO: Implement gh issue comment
            pass

    def _create_github_issue(self, content: str) -> str:
        """Create a new GitHub issue with requirements.

        Args:
            content: Requirements content

        Returns:
            Issue ID
        """
        return create_github_issue(content)

    def _update_github_issue(self, content: str) -> None:
        """Update existing GitHub issue with requirements.

        Args:
            content: Requirements content
        """
        update_github_issue(self.issue_id, content)

    def _generate_prompt(self) -> str:
        """Generate prompt for current iteration.

        Returns:
            Prompt string
        """
        if self.workflow_mode == WorkflowMode.GITHUB:
            return self._generate_github_prompt()
        else:
            return self._generate_local_prompt()

    def _get_non_technical_guidelines(self) -> str:
        """Get non-technical guidelines for PM.

        Returns:
            Guidelines string
        """
        return """**重要：絕對不可涉及技術細節！**
- ❌ 不要提及實作方式、技術架構、程式語言、框架、資料庫等
- ❌ 不要建議任何技術解決方案
- ✅ 只關注「用戶要什麼」「為什麼要」「預期效果是什麼」
- ✅ 從產品和業務角度思考"""

    def _get_status_code_prompt(self) -> str:
        """Get status code prompt.

        Returns:
            Status code prompt string
        """
        return generate_status_code_prompt(
            valid_codes=[
                PhaseStatusCode.CONFIRMED,
                PhaseStatusCode.NEED_CLARIFICATION,
                PhaseStatusCode.REJECTED,
            ],
            descriptions={
                PhaseStatusCode.CONFIRMED: "需求已經很清楚，可以進行開發",
                PhaseStatusCode.NEED_CLARIFICATION: "需求有不清楚的地方需要澄清",
                PhaseStatusCode.REJECTED: "需求有嚴重問題，無法進行",
            },
        )

    def _get_rigor_guidelines(self) -> str:
        """Get rigor level guidelines for PM.

        Returns:
            Rigor guidelines string
        """
        from aaf.core.types import SpecRigor

        if self.rigor == SpecRigor.LOW:
            return """**嚴謹程度：低（快速開發）**
- 只詢問最關鍵的資訊（核心功能是什麼）
- 不追問細節，可以讓開發者自行判斷的就不問
- 允許一些模糊地帶，相信開發者會做出合理選擇
- 如果使用者故事基本清楚，就可以確認
- 目標：快速進入開發，邊做邊調整"""
        elif self.rigor == SpecRigor.HIGH:
            return """**嚴謹程度：高（精確規格）**
- 詳細詢問所有細節（包括邊界情況、錯誤處理、特殊場景）
- 確保每個功能的輸入、輸出、行為都有明確定義
- 追問驗收標準，確保需求可測試
- 不允許任何模糊或「看情況」的描述
- 必須達到可以直接編寫測試案例的程度
- 目標：規格越清楚越好，減少後續溝通成本"""
        else:  # MEDIUM (default)
            return """**嚴謹程度：中（平衡模式）**
- 詢問重要的細節和關鍵場景
- 對於明顯需要澄清的地方提問，但不過度追問小細節
- 確保主要功能和預期行為清楚
- 對於次要細節可以接受合理的彈性
- 目標：在速度和精確度之間取得平衡"""

    def _generate_local_prompt(self) -> str:
        """Generate prompt for local workflow.

        Returns:
            Prompt string
        """
        non_technical = self._get_non_technical_guidelines()
        status_code_prompt = self._get_status_code_prompt()
        rigor_guidelines = self._get_rigor_guidelines()

        # Check if spec file exists
        spec_path = Path(self.spec_file)
        file_exists = spec_path.exists()

        if self.iteration == 1:
            if file_exists:
                # File exists - analyze and clarify
                return f"""分析 {self.spec_file} 的內容。

**你的角色：**
你是一位經驗豐富的 Product Manager (PM)，專注於需求澄清和產品規劃。
你不是軟體工程師，你的工作是確保需求清楚，避免開發者自己腦補。

這是第 {self.iteration} 輪需求澄清。

**你的職責：**
1. 仔細閱讀需求文件，找出所有不清楚、模糊、可能讓開發者自己腦補的地方
2. **以 PM 的身份**用對話方式向用戶提問，確認所有必要資訊
3. 如果需求已經很清楚，就說清楚了，不要硬湊問題

{rigor_guidelines}

{non_technical}

{status_code_prompt}

**如果需要澄清需求（status: NEED_CLARIFICATION）：**
1. 使用 Write tool 將以下內容寫入 {self.spec_file}：
   - 「## 使用者故事」- 原始的使用者故事（保持不變，除非用戶要求變更）
   - 「## 目前的需求規格」- 列出目前已知的所有需求內容
   - 「## 待釐清的問題」- 以 PM 的身份用對話方式向用戶提問
2. 寫完檔案後，只回傳：NEED_CLARIFICATION

記住：你是 PM，不是工程師，不要提技術細節！

**如果需求已清楚（status: CONFIRMED）：**
1. 使用 Write tool 將完整需求規格文件寫入 {self.spec_file}，格式：
   - 「## 使用者故事」- 原始的使用者故事
   - 「## 需求規格」- 完整的需求內容，包含：功能描述、使用場景、預期行為、驗收標準
2. 寫完檔案後，只回傳：CONFIRMED
"""
            else:
                # File doesn't exist but user story should have been created
                return f"""分析 {self.spec_file} 中的使用者故事。

**你的角色：**
你是一位經驗豐富的 Product Manager (PM)，負責將使用者故事轉換成完整的需求文件。
你不是軟體工程師，你的工作是確保需求清楚。

這是第 {self.iteration} 輪需求澄清。

{rigor_guidelines}

{non_technical}

{status_code_prompt}

**如果需要更多資訊（status: NEED_CLARIFICATION）：**
1. 使用 Write tool 將以下內容寫入 {self.spec_file}：
   - 「## 使用者故事」- 原始的使用者故事（保持不變，除非用戶要求變更）
   - 「## 目前的需求規格」- 整理目前從使用者故事得知的需求
   - 「## 待釐清的問題」- 以 PM 的身份提出具體問題（使用場景、預期行為、驗收標準）
2. 寫完檔案後，只回傳：NEED_CLARIFICATION

記住：你是 PM，不要問技術實作問題！

**如果資訊已足夠（status: CONFIRMED）：**
1. 使用 Write tool 將完整需求文件寫入 {self.spec_file}，格式：
   - 「## 使用者故事」- 原始的使用者故事
   - 「## 需求規格」- 完整的需求內容
2. 寫完檔案後，只回傳：CONFIRMED
"""
        else:
            # Iteration 2+: Include context file reference
            context_path = self.history_dir / "context.md"
            context_reference = f"""
**對話歷史：**
請先閱讀 {context_path} 了解之前的對話記錄和已確定的需求。
"""

            # Add restriction for iteration 4+
            restriction = ""
            if self.iteration >= 4:
                restriction = f"""
⚠️ **重要限制：**
- 你現在是第 {self.iteration} 輪，只能針對「待解答的問題」繼續追問
- **不可以提出新的問題**
- 只能深入釐清已經提出的問題
"""

            return f"""繼續分析 {self.spec_file} 的最新版本。

**你的角色：**
你是一位經驗豐富的 Product Manager (PM)，專注於需求澄清。
你不是軟體工程師，不要提技術實作細節。

這是第 {self.iteration} 輪需求澄清。請檢查需求文件的最新版本。
{context_reference}
{rigor_guidelines}

{non_technical}

{status_code_prompt}
{restriction}
**如果仍需澄清（status: NEED_CLARIFICATION）：**
1. 使用 Write tool 將以下內容寫入 {self.spec_file}：
   - 「## 使用者故事」- 原始的使用者故事（保持不變，除非用戶要求變更）
   - 「## 目前的需求規格」- 整合之前的對話和用戶最新回答，列出目前已知的完整需求
   - 「## 待釐清的問題」- 以 PM 的身份繼續用對話方式提問
2. 寫完檔案後，只回傳：NEED_CLARIFICATION

**如果需求已清楚（status: CONFIRMED）：**
1. 使用 Write tool 將完整需求規格文件寫入 {self.spec_file}，格式：
   - 「## 使用者故事」- 原始的使用者故事
   - 「## 需求規格」- 完整需求（整合所有已確認的內容）
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
                PhaseStatusCode.CONFIRMED: "需求已經很清楚，可以進行開發",
                PhaseStatusCode.NEED_CLARIFICATION: "需求有不清楚的地方需要澄清",
                PhaseStatusCode.REJECTED: "需求有嚴重問題，無法進行",
            },
        )

        if self.iteration == 1:
            return f"""這是第 {self.iteration} 輪需求分析。

請用 `gh issue view {self.issue_id}` 讀取 Issue 內容，仔細分析需求，找出所有不清楚、模糊、可能讓開發者自己腦補的地方。

{status_code_prompt}

**如果有需求問題需要澄清：**
用最精簡的方式條列問題，不要給任何建議。

**如果需求已經很清楚，確認完成：**
回應確認訊息。
"""
        else:
            return f"""這是第 {self.iteration} 輪需求分析。

請用 `gh issue view {self.issue_id}` 檢視 Issue 的最新內容。

{status_code_prompt}

**如果有需求問題需要澄清：**
用最精簡的方式條列問題，不要給任何建議。

**如果需求已經很清楚，確認完成：**
回應確認訊息。
"""

    def _save_iteration_history(
        self,
        pm_response: str,
        user_response: str,
        status: PhaseStatusCode,
    ) -> None:
        """Save iteration history to JSON file.

        Args:
            pm_response: PM's response (questions or final requirements)
            user_response: User's response to PM's questions
            status: Status code for this iteration
        """
        # Create history directory
        self.history_dir.mkdir(parents=True, exist_ok=True)

        # Create iteration record
        iteration_data = {
            "iteration": self.iteration,
            "timestamp": datetime.now().isoformat(),
            "status": status.value,
            "pm_response": pm_response,
            "user_response": user_response,
            "confirmed_requirements": self.confirmed_requirements.copy(),
            "pending_questions": self.pending_questions.copy(),
        }

        # Save to JSON file
        iteration_file = self.history_dir / f"iteration_{self.iteration:03d}.json"
        with open(iteration_file, "w", encoding="utf-8") as f:
            json.dump(iteration_data, f, ensure_ascii=False, indent=2)

        # Update context.md for agent
        self._update_context_file()

    def _update_context_file(self) -> None:
        """Update context.md with conversation history for agent."""
        # Ensure history directory exists
        self.history_dir.mkdir(parents=True, exist_ok=True)

        context_lines = ["# 需求澄清歷史\n"]

        # Confirmed requirements section
        context_lines.append("## 已確定的需求\n")
        if self.confirmed_requirements:
            for req in self.confirmed_requirements:
                context_lines.append(f"{req}\n")
        else:
            context_lines.append("(尚無已確定的需求)\n")
        context_lines.append("\n")

        # Pending questions section
        context_lines.append("## 待解答的問題\n")
        if self.pending_questions:
            for i, question in enumerate(self.pending_questions, 1):
                context_lines.append(f"{i}. {question}\n")
        else:
            context_lines.append("(無待解答問題)\n")
        context_lines.append("\n")

        # Conversation history
        context_lines.append("## 對話歷史\n")
        for entry in self.conversation_history:
            context_lines.append(f"### 第 {entry['iteration']} 輪\n")
            context_lines.append(f"**PM 問題：**\n{entry['pm_response']}\n\n")
            if entry.get('user_response'):
                context_lines.append(f"**用戶回答：**\n{entry['user_response']}\n\n")
        context_lines.append("\n")

        # Important notes
        context_lines.append("## 重要提示\n")
        context_lines.append(f"- 目前是第 {self.iteration} 輪\n")
        if self.iteration >= 4:
            context_lines.append("- ⚠️ **限制：只能針對現有問題繼續追問，不可提出新問題**\n")

        # Save context file
        context_file = self.history_dir / "context.md"
        context_file.write_text("".join(context_lines), encoding="utf-8")

    def _load_history(self) -> None:
        """Load conversation history from previous iterations."""
        if not self.history_dir.exists():
            return

        # Find all iteration files
        iteration_files = sorted(self.history_dir.glob("iteration_*.json"))

        for iteration_file in iteration_files:
            with open(iteration_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Restore conversation history
            self.conversation_history.append({
                "iteration": data["iteration"],
                "pm_response": data["pm_response"],
                "user_response": data["user_response"],
                "status": data["status"],
            })

            # Restore confirmed requirements
            if data.get("confirmed_requirements"):
                self.confirmed_requirements = data["confirmed_requirements"]

            # Restore pending questions
            if data.get("pending_questions"):
                self.pending_questions = data["pending_questions"]

            # Update iteration counter
            if data["iteration"] >= self.iteration:
                self.iteration = data["iteration"]


    def _get_status_file(self) -> Path:
        """Get the path to status.json file.

        Returns:
            Path to status.json in .aaf/issues/{issue_name}/spec/status.json
        """
        return self.history_dir.parent / "status.json"

    def get_status_file(self) -> Path:
        """Public method to get status file path for workflow integration.

        Returns:
            Path to status.json
        """
        return self._get_status_file()

    def _save_progress(self, status_code: PhaseStatusCode) -> None:
        """Save phase progress to status.json.
        
        Args:
            status_code: Phase status code (CONFIRMED, NEED_CLARIFICATION, etc.)
        """
        status_file = self._get_status_file()
        status_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Determine phase status
        phase_status = PhaseStatus.COMPLETED if status_code == PhaseStatusCode.CONFIRMED else PhaseStatus.IN_PROGRESS
        
        progress = PhaseProgress(
            phase="spec",
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
        status_file = self._get_status_file()
        if not status_file.exists():
            return None
        
        with open(status_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        return PhaseProgress.from_dict(data)
