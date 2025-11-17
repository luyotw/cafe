"""Specification phase (requirements clarification)."""

import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from cafe.agents.manager import AgentManager
from cafe.core.permission import PermissionHandler
from cafe.core.phase import Phase
from cafe.core.status_codes import PhaseStatusCode, StatusCodeParser, generate_status_code_prompt
from cafe.core.types import PhaseProgress, PhaseResult, PhaseStatus, WorkflowMode
from cafe.ui.display import Display

# Maximum number of clarification iterations to prevent infinite loops
MAX_CLARIFICATION_ITERATIONS = 10


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
        user_input: str = "",
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
            user_input: User input for non-interactive mode (default: "")
        """
        super().__init__(interactive=interactive)
        
        from cafe.core.types import SpecRigor

        self.agent_manager = agent_manager
        self.permission_handler = permission_handler
        self.spec_file = spec_file
        self.workflow_mode = workflow_mode
        self.issue_id = issue_id
        self.pm_agent = pm_agent
        self.user_input = user_input
        self.phase_name = "spec"  # For base class progress tracking

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
            # Derive from spec_file path: .cafe/issues/{issue_name}/spec/spec.md
            spec_path = Path(spec_file)
            self.issue_name = spec_path.parent.parent.name

        # History directory for spec phase
        # Path: .cafe/issues/{issue_name}/spec/history
        spec_path = Path(self.spec_file)
        issue_dir = spec_path.parent.parent  # .cafe/issues/{issue_name}
        self.history_dir = issue_dir / "spec" / "history"

        # Track requirements and questions
        self.confirmed_requirements = []
        self.pending_questions = []

        # Initialize display for better input handling
        self.display = Display()

        # Restore state from last iteration file (if resuming)
        self.iteration = self._load_iteration_counter()

        # Load phase-specific data from last iteration
        if self.iteration > 0 and self.history_dir.exists():
            iteration_files = sorted(self.history_dir.glob("iteration_*.json"))
            if iteration_files:
                last_iteration_file = iteration_files[-1]
                with open(last_iteration_file, "r", encoding="utf-8") as f:
                    last_data = json.load(f)
                if "confirmed_requirements" in last_data:
                    self.confirmed_requirements = last_data["confirmed_requirements"]
                if "pending_questions" in last_data:
                    self.pending_questions = last_data["pending_questions"]

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
            already_completed = self._check_if_already_completed([PhaseStatusCode.CONFIRMED])
            if already_completed:
                return already_completed

            # Validate inputs
            # Note: GitHub mode can now work without issue_id (will create new issue)

            if self.workflow_mode == WorkflowMode.LOCAL:
                # Check if spec file exists
                spec_path = Path(self.spec_file)
                if spec_path.exists():
                    # Backup original spec if exists
                    self._backup_spec(spec_path)
                elif self.iteration == 0:
                    # File doesn't exist AND no history - get initial user story
                    if self.interactive:
                        self._prompt_for_user_story()
                    else:
                        # Non-interactive mode: use user_input if provided, otherwise read from stdin
                        if self.user_input:
                            user_story = self.user_input.strip()
                        else:
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

            # Requirements clarification loop
            while True:
                self.iteration += 1

                # Safety check: prevent infinite loops
                max_iterations_result = self._check_max_iterations(
                    MAX_CLARIFICATION_ITERATIONS,
                    "Requirements clarification"
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

                # Prepare allowed tools with write/edit permission for spec file
                spec_file_path = Path(self.spec_file)

                # Convert to project-relative path (git ignore format: / prefix)
                import os
                project_root = Path(os.getcwd())
                try:
                    relative_path = spec_file_path.relative_to(project_root)
                    spec_file_pattern = f"/{relative_path}"
                except ValueError:
                    # If path is not relative to cwd, use absolute path
                    spec_file_pattern = str(spec_file_path)

                # Merge base tools with previous iteration's tools (if any)
                base_allowed_tools = [
                    "read",
                    "grep",
                    "glob",
                    "ls",
                    "web_fetch",
                    "web_search",
                    f"write({spec_file_pattern})",
                    f"edit({spec_file_pattern})",
                ]
                allowed_tools = self._merge_allowed_tools(base_allowed_tools)

                # Execute full agent interaction cycle (generate prompt, execute, handle status)
                result, response = self._execute_and_handle_agent_response(
                    agent_name=self.pm_agent,
                    user_input=current_user_input,
                    valid_status_codes=[
                        PhaseStatusCode.CONFIRMED,
                        PhaseStatusCode.NEED_CLARIFICATION,
                        PhaseStatusCode.REJECTED,
                    ],
                    allowed_tools=allowed_tools,
                    continue_codes=[PhaseStatusCode.NEED_CLARIFICATION],
                )

                # In mock mode or if agent doesn't use write tool, write spec from response
                self._ensure_spec_file_written(response)

                # Phase-specific post-processing: Sync spec to GitHub (no-op in local mode)
                self._sync_spec_to_github("")

                if result:
                    return result
                # If result is None, continue to next iteration

        except KeyboardInterrupt:
            # User paused with Ctrl+C - save progress and allow resume
            print("\n\n⏸️  Paused by user (Ctrl+C).")
            print(f"💾 Progress saved. Current iteration: {self.iteration}")
            print(f"📝 To resume, run: cafe spec {self.issue_name}")
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

    def _get_completion_data(self) -> dict:
        """取得 phase 完成時的額外資料（提供給 base class 的 _handle_standard_status_codes）。

        Returns:
            包含 phase-specific 資料的 dict，會被合併到 PhaseResult.data 中
        """
        data = {}
        # Add issue_id if GitHub mode and created new issue
        if self.workflow_mode == WorkflowMode.GITHUB and hasattr(self, '_created_issue_id'):
            data["issue_id"] = self._created_issue_id
        return data

    def _prepare_user_input_for_iteration(self) -> "PhaseResult | str":
        """準備當前迭代的 user input。

        在最開始就取得所有需要的用戶輸入：
        - Interactive: 從 stdin 詢問用戶
        - Non-interactive: 從 self.user_input 取得

        之後的處理邏輯完全相同，不再區分 interactive/non-interactive。

        Returns:
            PhaseResult: 如果需要結束/暫停 phase
            str: 用戶輸入內容（供 agent 使用）
        """
        # Iteration 1: user_input is the initial user story from spec file
        if self.iteration == 1:
            spec_file_path = Path(self.spec_file)
            return spec_file_path.read_text() if spec_file_path.exists() else ""

        # Iteration 2+: Check if current iteration was interrupted (has user_input but no response)
        current_data = self._load_current_iteration_data()
        if current_data and current_data.get("user_input") and not current_data.get("response"):
            # 恢復被中斷的 iteration，直接使用已儲存的 user_input
            return current_data["user_input"]

        # Iteration 2+: Display current spec content (interactive only)
        if self.interactive:
            self._display_current_spec()

        # Iteration 2+: Load previous iteration data
        prev_data = self._load_previous_iteration_data()
        if not prev_data:
            return ""

        prev_status = prev_data.get("status_code", "")

        # 根據上一輪狀態，取得用戶輸入
        if prev_status == "CAFE_NEED_CLARIFICATION":
            return self._handle_need_clarification_input(prev_data, agent_display_name="PM")
        else:
            return ""

    def _display_current_spec(self) -> None:
        """顯示目前的 spec.md 內容（僅 interactive 模式）。"""
        spec_file = Path(self.spec_file)
        spec_content = spec_file.read_text() if spec_file.exists() else "（檔案未產生）"

        # Get PM CLI info for display
        pm_cli = self.agent_manager.get_agent_config(self.pm_agent).cli.value

        print(f"\n{'='*60}")
        print(f"PM ({self.pm_agent} by {pm_cli}) - 目前規格內容 (Iteration {self.iteration - 1}):")
        print(f"{'='*60}")
        print(spec_content)
        print(f"{'='*60}\n")

    def _prompt_for_rigor(self) -> None:
        """Prompt user to select rigor level if not already set."""
        from cafe.core.types import SpecRigor

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
        """Prompt user to write initial requirement when no requirements file exists."""
        print("\n" + "="*70)
        print("請描述你的需求：")
        print("="*70)
        print()
        print("建議以使用者故事的方式撰寫：")
        print("   格式：身為[角色]，我想要[功能]，以便[目的/價值]")
        print()
        print("   範例：")
        print("   - 身為產品經理，我想要快速了解專案進度，以便向團隊報告開發狀態")
        print("   - 身為用戶，我想要看到清楚的錯誤訊息，以便知道哪裡出問題並如何修正")
        print()
        print("也可以用一般方式描述需求：")
        print("   - 新增一個匯出 CSV 的功能")
        print("   - 修正登入頁面無法提交的 bug")
        print("   - 優化首頁載入速度")
        print()

        # Get user's requirement using Display for better Unicode support
        user_requirement = self.display.get_multiline_input("請輸入你的需求").strip()

        if not user_requirement:
            raise ValueError("未提供需求，無法繼續")

        # Save requirement as initial spec
        spec_path = Path(self.spec_file)
        spec_path.parent.mkdir(parents=True, exist_ok=True)
        spec_path.write_text(f"# 初始需求\n\n{user_requirement}\n")

        print()
        print("✅ 需求已記錄，開始需求澄清...")
        print()

    def _backup_spec(self, spec_path: Path) -> None:
        """Backup original spec file.

        Args:
            spec_path: Path to spec file
        """
        backup_path = Path(f"{spec_path}.backup")
        if not backup_path.exists():
            backup_path.write_text(spec_path.read_text())

    def _ensure_spec_file_written(self, response: str) -> None:
        """確保 spec 檔案已寫入（用於 mock 模式或 agent 未使用 write tool）。
        
        在 mock 模式下，agent 不會實際呼叫 write tool，所以需要從 response 中提取內容並寫入。
        
        Args:
            response: Agent 回應內容
        """
        import os
        
        # 只在 mock 模式下處理
        if not os.getenv("CAFE_MOCK_AGENTS"):
            return
            
        # 提取狀態碼後的內容
        lines = response.strip().split("\n")
        if not lines:
            return
            
        # 跳過第一行（狀態碼）和空行
        content_lines = []
        skip_first = True
        for line in lines:
            if skip_first and line.startswith("CAFE_"):
                continue
            skip_first = False
            content_lines.append(line)
        
        content = "\n".join(content_lines).strip()
        if not content:
            return
            
        # 寫入檔案
        spec_path = Path(self.spec_file)
        spec_path.parent.mkdir(parents=True, exist_ok=True)
        spec_path.write_text(content, encoding="utf-8")

    def _sync_spec_to_github(self, response: str) -> None:
        """同步 spec 檔案到 GitHub issue（僅 GitHub workflow 模式）。

        注意：此方法**不負責寫入 spec 檔案**。PM agent 已透過 Write tool 直接寫入 spec_file。

        此方法的用途：
        - Local mode: 無動作（agent 已寫入檔案，無需額外處理）
        - GitHub mode: 將本地 spec_file 內容同步到 GitHub issue

        Args:
            response: Agent 回應內容（未使用，因為 agent 已透過 Write tool 寫入檔案）
        """
        spec_path = Path(self.spec_file)
        if not spec_path.exists():
            return  # Agent 尚未寫入檔案

        if self.workflow_mode == WorkflowMode.GITHUB:
            # 將本地檔案內容同步到 GitHub issue
            content = spec_path.read_text()
            if not self.issue_id and not hasattr(self, '_created_issue_id'):
                # 首次建立 GitHub issue
                self._created_issue_id = create_github_issue(content)
            elif hasattr(self, '_created_issue_id'):
                # 更新先前建立的 issue
                update_github_issue(self._created_issue_id, content)
            else:
                # 更新既有的 issue
                update_github_issue(self.issue_id, content)

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

    def _generate_prompt(self, user_input: str = "") -> str:
        """Generate prompt for current iteration.

        Args:
            user_input: User's response/clarification for this iteration

        Returns:
            Prompt string
        """
        if self.workflow_mode == WorkflowMode.GITHUB:
            return self._generate_github_prompt(user_input)
        else:
            return self._generate_local_prompt(user_input)

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
        from cafe.core.types import SpecRigor

        if self.rigor == SpecRigor.LOW:
            return """**嚴謹程度：低（快速開發）**
- 只詢問最關鍵的資訊（核心功能是什麼）
- 不追問細節，可以讓開發者自行判斷的就不問
- 允許一些模糊地帶，相信開發者會做出合理選擇
- 如果需求基本清楚，就可以確認
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

    def _generate_local_prompt(self, user_input: str = "") -> str:
        """Generate prompt for local workflow.

        Args:
            user_input: User's response/clarification for this iteration

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

**如果需要澄清需求（status: CAFE_NEED_CLARIFICATION）：**
使用 Write tool 將以下內容寫入 {self.spec_file}：
   - 「## 使用者故事」- 用戶撰寫的使用者故事或由自動由需求描述產生的使用者故事
   - 「## 目前的需求規格」- 列出目前已知的所有需求內容
   - 「## 待釐清的問題」- 以 PM 的身份用對話方式向用戶提問

記住：你是 PM，不是工程師，不要提技術細節！

**如果需求已清楚（status: CAFE_CONFIRMED）：**
使用 Write tool 將完整需求規格文件寫入 {self.spec_file}，格式：
   - 「## 使用者故事」- 用戶撰寫的使用者故事或由自動由需求描述產生的使用者故事
   - 「## 需求規格」- 完整的需求內容，包含：功能描述、使用場景、預期行為、驗收標準
"""
            else:
                # File doesn't exist but user story should have been created
                return f"""分析 {self.spec_file} 中的使用者故事。

**你的角色：**
你是一位經驗豐富的 Product Manager (PM)，負責將用戶的需求寫成使用者故事，並轉換成完整的需求文件。
你不是軟體工程師，你的工作是確保需求清楚。

這是第 {self.iteration} 輪需求澄清。

{rigor_guidelines}

{non_technical}

{status_code_prompt}

**如果需要更多資訊（status: CAFE_NEED_CLARIFICATION）：**
使用 Write tool 將以下內容寫入 {self.spec_file}：
   - 「## 使用者故事」- 用戶撰寫的使用者故事或由自動由需求描述產生的使用者故事
   - 「## 目前的需求規格」- 整理目前從使用者故事得知的需求
   - 「## 待釐清的問題」- 以 PM 的身份提出具體問題（使用場景、預期行為、驗收標準）

記住：你是 PM，不要問技術實作問題！

**如果資訊已足夠（status: CAFE_CONFIRMED）：**
使用 Write tool 將完整需求文件寫入 {self.spec_file}，格式：
   - 「## 使用者故事」- 用戶撰寫的使用者故事或由自動由需求描述產生的使用者故事
   - 「## 需求規格」- 完整的需求內容
"""
        else:
            # Iteration 2+: Include user's response
            user_response_section = ""
            if user_input:
                user_response_section = f"""
**使用者的回答：**
{user_input}

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
{user_response_section}
{rigor_guidelines}

{non_technical}

{status_code_prompt}
{restriction}
**如果仍需澄清（status: CAFE_NEED_CLARIFICATION）：**
使用 Write tool 將以下內容寫入 {self.spec_file}：
   - 「## 使用者故事」- 用戶撰寫的使用者故事或由自動由需求描述產生的使用者故事
   - 「## 目前的需求規格」- 整合之前的對話和用戶最新回答，列出目前已知的完整需求
   - 「## 待釐清的問題」- 以 PM 的身份繼續用對話方式提問

**如果需求已清楚（status: CAFE_CONFIRMED）：**
使用 Write tool 將完整需求規格文件寫入 {self.spec_file}，格式：
   - 「## 使用者故事」- 用戶撰寫的使用者故事或由自動由需求描述產生的使用者故事
   - 「## 需求規格」- 完整需求（整合所有已確認的內容）
"""

    def _generate_github_prompt(self, user_input: str = "") -> str:
        """Generate prompt for GitHub workflow.

        Args:
            user_input: User's response/clarification for this iteration

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

    def get_status_file(self) -> Path:
        """Public method to get status file path for workflow integration.

        Returns:
            Path to status.json
        """
        return self._get_status_file()
