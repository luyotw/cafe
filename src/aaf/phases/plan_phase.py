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
            interactive: Whether to allow interactive prompts (default: True)
        """
        self.agent_manager = agent_manager
        self.permission_handler = permission_handler
        self.spec_file = spec_file
        self.workflow_mode = workflow_mode
        self.issue_id = issue_id
        self.dev_agent = dev_agent
        self.interactive = interactive
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

            # Implementation plan loop
            while True:
                self.iteration += 1

                # Generate prompt for this iteration
                prompt = self._generate_prompt()

                # Execute developer agent
                response = self.agent_manager.execute(
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
                        prompt=prompt,
                        response=response,
                        status_code=status_code,
                    )
                    self._save_progress(status_code)

                # Handle status codes
                if status_code == PhaseStatusCode.CONFIRMED:
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
                    # Continue to next iteration for clarification
                    continue
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

        if self.iteration == 1:
            return f"""分析 {self.spec_file} 並規劃實作步驟。

**你的角色：**
你是一位經驗豐富的 Developer，負責根據需求規格和開發指南，規劃詳細的實作步驟。

這是第 {self.iteration} 輪實作分析。

請仔細閱讀需求文件（{self.spec_file}）和 {plan_file_path} 中的開發指南，規劃詳細的實作步驟。

{status_code_prompt}

**如果需要更多資訊（status: NEED_CLARIFICATION）：**
1. 使用 Write tool 將以下內容寫入 {plan_file_path}：
   - 「## 開發指南」- 保留原有的開發指南內容（不要修改）
   - 「## 實作計畫」- 目前的實作分析內容
   - 「## 待確認問題」- 列出需要確認的技術問題
2. 寫完檔案後，只回傳：NEED_CLARIFICATION

**如果分析完成（status: CONFIRMED）：**
1. 使用 Write tool 將完整實作計畫寫入 {plan_file_path}，格式：
   - 「## 開發指南」- 保留原有的開發指南內容（不要修改）
   - 「## 技術分析」- 技術可行性分析
   - 「## 實作步驟」- 詳細的實作步驟
   - 「## 技術細節」- 需要注意的技術細節
2. 寫完檔案後，只回傳：CONFIRMED
"""
        else:
            return f"""繼續分析 {self.spec_file} 的最新版本。

**你的角色：**
你是一位經驗豐富的 Developer，負責根據需求規格和開發指南，規劃詳細的實作步驟。

這是第 {self.iteration} 輪實作分析。

請檢查 {plan_file_path} 的最新版本，繼續完善實作計畫。

{status_code_prompt}

**如果仍需確認（status: NEED_CLARIFICATION）：**
1. 使用 Write tool 更新 {plan_file_path}：
   - 「## 開發指南」- 保留原有的開發指南內容（不要修改）
   - 「## 實作計畫」- 更新的實作分析內容
   - 「## 待確認問題」- 列出需要確認的技術問題
2. 寫完檔案後，只回傳：NEED_CLARIFICATION

**如果分析完成（status: CONFIRMED）：**
1. 使用 Write tool 將完整實作計畫寫入 {plan_file_path}，格式：
   - 「## 開發指南」- 保留原有的開發指南內容（不要修改）
   - 「## 技術分析」- 技術可行性分析
   - 「## 實作步驟」- 詳細的實作步驟
   - 「## 技術細節」- 需要注意的技術細節
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
        prompt: str,
        response: str,
        status_code: PhaseStatusCode,
    ) -> None:
        """Save iteration history to JSON file.

        Args:
            prompt: The prompt sent to agent
            response: The agent's response
            status_code: Status code from response
        """
        # Create history directory if it doesn't exist
        self.history_dir.mkdir(parents=True, exist_ok=True)

        history_file = self.history_dir / f"{self.iteration:03d}.json"

        history_data = {
            "iteration": self.iteration,
            "timestamp": datetime.now().isoformat(),
            "dev_agent": self.dev_agent,
            "prompt": prompt,
            "response": response,
            "status_code": status_code.value,
        }

        with open(history_file, 'w', encoding='utf-8') as f:
            json.dump(history_data, f, ensure_ascii=False, indent=2)

        # Add to conversation history in memory
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
