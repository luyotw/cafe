"""Development phase."""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from aaf.agents.manager import AgentManager
from aaf.core.git import GitOperations
from aaf.core.permission import PermissionHandler
from aaf.core.phase import Phase
from aaf.core.status_codes import PhaseStatusCode, StatusCodeParser, generate_status_code_prompt
from aaf.core.types import PhaseProgress, PhaseResult, PhaseStatus, WorkflowMode
from aaf.ui.display import Display


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
        """
        self.agent_manager = agent_manager
        self.permission_handler = permission_handler
        self.git_ops = git_ops
        self.spec_file = spec_file
        self.plan_file = plan_file
        self.workflow_mode = workflow_mode
        self.issue_id = issue_id
        self.dev_agent = dev_agent
        self.interactive = interactive

        # Iteration tracking
        self.iteration = 0

        # Determine issue name for history tracking
        if issue_name:
            self.issue_name = issue_name
        else:
            # Derive from spec_file path: .aaf/issues/{issue_name}/spec/spec.md
            spec_path = Path(spec_file)
            self.issue_name = spec_path.parent.parent.name

        # History directory for develop phase
        # Path: .aaf/issues/{issue_name}/develop/history
        spec_path = Path(self.spec_file)
        issue_dir = spec_path.parent.parent  # .aaf/issues/{issue_name}
        self.history_dir = issue_dir / "develop" / "history"

        # Track conversation history
        self.conversation_history: List[Dict[str, Any]] = []

        # Track user responses for permission requests
        self.user_responses: List[str] = []

        # Initialize display
        self.display = Display()

        # Load existing history if available
        self._load_history()

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
        issue_dir = spec_path.parent.parent  # .aaf/issues/{issue_name}
        return issue_dir / "review" / "review.md"

    def _check_review_feedback_exists(self) -> bool:
        """檢查是否存在 review feedback.

        Returns:
            True if review.md exists, False otherwise
        """
        review_file = self._get_review_file_path()
        return review_file.exists()

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
            self.conversation_history.append(data)

            # Update iteration counter
            if data["iteration"] >= self.iteration:
                self.iteration = data["iteration"]

            # Restore user responses if they exist
            if "user_response" in data and data["user_response"]:
                self.user_responses.append(data["user_response"])

    def _save_history(
        self,
        user_input: str,
        response: str,
        status_code: PhaseStatusCode,
        user_response: Optional[str] = None,
    ) -> None:
        """Save iteration history to JSON file.

        Args:
            user_input: User's input for this iteration
            response: Agent's response
            status_code: Status code for this iteration
            user_response: User's response to agent's request (for NEED_PERMISSION)
        """
        # Create history directory
        self.history_dir.mkdir(parents=True, exist_ok=True)

        # Create iteration record
        iteration_data = {
            "iteration": self.iteration,
            "timestamp": datetime.now().isoformat(),
            "user_input": user_input,
            "response": response,
            "status_code": status_code.value,
        }

        # Add user response if provided
        if user_response:
            iteration_data["user_response"] = user_response

        # Save to JSON file
        iteration_file = self.history_dir / f"iteration_{self.iteration:03d}.json"
        with open(iteration_file, "w", encoding="utf-8") as f:
            json.dump(iteration_data, f, ensure_ascii=False, indent=2)

    def _save_progress(self, status_code: PhaseStatusCode) -> None:
        """Save phase progress to status.json.

        Args:
            status_code: Phase status code
        """
        status_file = self.history_dir.parent / "status.json"
        status_file.parent.mkdir(parents=True, exist_ok=True)

        # Determine phase status
        phase_status = PhaseStatus.COMPLETED if status_code == PhaseStatusCode.CONFIRMED else PhaseStatus.IN_PROGRESS

        progress = PhaseProgress(
            phase="develop",
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
            PhaseProgress if exists, None otherwise
        """
        status_file = self.history_dir.parent / "status.json"
        if not status_file.exists():
            return None

        try:
            with open(status_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return PhaseProgress.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            return None

    def _generate_prompt(self) -> str:
        """Generate prompt for current iteration.

        Returns:
            Prompt string
        """
        if self.iteration == 1:
            # First iteration: provide spec.md and plan.md paths
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

            return f"""請按照實作計畫執行開發工作。

**你的角色：**
你是一位經驗豐富的 Developer，負責根據需求規格和實作計畫進行開發。

**檔案路徑：**
- 需求規格：{self.spec_file}
- 實作計畫：{self.plan_file}

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

            return f"""繼續執行開發工作。

**你的角色：**
你是一位經驗豐富的 Developer，負責根據需求規格和實作計畫進行開發。

這是第 {self.iteration} 輪開發。

**歷史記錄：**
{history_text}

**檔案路徑：**
- 需求規格：{self.spec_file}
- 實作計畫：{self.plan_file}

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
        """Execute development phase - single iteration only.
        
        Each invocation executes ONE iteration of development work.
        For multiple iterations, the user should run the command multiple times.
        
        Returns:
            Phase result
        """
        try:
            # Check plan.md exists
            if not self._check_plan_exists():
                return PhaseResult(
                    status=PhaseStatus.FAILED,
                    message=f"Plan file not found: {self.plan_file}. Please run 'aaf plan' first.",
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

            # Check if phase is already completed
            existing_progress = self._load_progress()
            if existing_progress and existing_progress.status == PhaseStatus.COMPLETED:
                # Phase already completed, return existing result
                return PhaseResult(
                    status=PhaseStatus.COMPLETED,
                    message=f"Development already completed in {existing_progress.iteration} iteration(s)",
                    data={
                        "branch": self._get_branch_name(),
                        "iterations": existing_progress.iteration,
                        "status_code": existing_progress.status_code,
                    },
                )

            # Create or checkout branch
            branch_name = self._get_branch_name()
            if self.git_ops.branch_exists(branch_name):
                self.git_ops.checkout_branch(branch_name)
            else:
                self.git_ops.create_branch(branch_name)

            # Single iteration execution
            # Increment iteration counter
            self.iteration += 1
            
            # Prepare user_input for this iteration
            # Note: We don't read files - agent will read them using Read tool
            current_user_input = ""
            
            # Check if there's a pending NEED_PERMISSION from previous run
            if (self.conversation_history and
                self.conversation_history[-1].get("status_code") == "NEED_PERMISSION" and
                "user_response" not in self.conversation_history[-1]):
                # Handle pending permission request
                last_iteration = self.conversation_history[-1]
                
                if self.interactive:
                    print(f"\n{'='*60}")
                    print(f"Dev ({self.dev_agent}) - Iteration {last_iteration['iteration']} (resuming):")
                    print(f"{'='*60}")
                    print(last_iteration['response'])
                    print(f"{'='*60}\n")
                    
                    # Ask user for decision
                    print("開發者請求工具權限。請選擇：")
                    print("  [c] confirm - 同意授權，繼續執行")
                    print("  [r] reject - 拒絕授權，終止開發")
                    print("  [m] modify - 給予其他提示或建議")
                    
                    while True:
                        choice = input("\n請選擇 [c/r/m]: ").strip().lower()
                        
                        if choice == 'c':
                            print("\n✅ 權限已授予")
                            current_user_input = "授權同意"
                            break
                        elif choice == 'r':
                            print("\n❌ 權限被拒絕，Phase 終止。")
                            return PhaseResult(
                                status=PhaseStatus.FAILED,
                                message=f"Permission denied by user in iteration {last_iteration['iteration']}",
                                data={
                                    "iterations": last_iteration['iteration'],
                                    "last_response": last_iteration['response'],
                                    "status_code": "USER_REJECTED",
                                },
                            )
                        elif choice == 'm':
                            modification_request = self.display.get_multiline_input("請輸入提示或建議")
                            if not modification_request.strip():
                                print("\n⚠️  沒有輸入內容，請重新選擇。")
                                continue
                            print("\n✅ 已收到您的建議")
                            current_user_input = modification_request
                            break
                        else:
                            print("❌ 無效選擇，請輸入 c, r, 或 m")
                    
                    # Save user response to the previous iteration
                    last_iteration['user_response'] = current_user_input
                    iteration_file = self.history_dir / f"iteration_{last_iteration['iteration']:03d}.json"
                    with open(iteration_file, "w", encoding="utf-8") as f:
                        json.dump(last_iteration, f, ensure_ascii=False, indent=2)
                else:
                    return PhaseResult(
                        status=PhaseStatus.FAILED,
                        message="Permission required but running in non-interactive mode",
                        data={
                            "iterations": last_iteration['iteration'],
                            "last_response": last_iteration['response'],
                        },
                    )

            # Generate prompt for this iteration
            prompt = self._generate_prompt()

            # Execute developer agent with allowed tools
            response = self.agent_manager.execute(
                self.dev_agent,
                prompt,
                allowed_tools=["write", "read", "shell"]
            )

            # Extract status code from response
            status_code = StatusCodeParser.extract(
                response,
                valid_codes=[
                    PhaseStatusCode.CONFIRMED,
                    PhaseStatusCode.NEED_PERMISSION,
                ],
            )

            # Handle status codes
            if status_code == PhaseStatusCode.CONFIRMED:
                # Development completed
                self._save_history(
                    user_input=current_user_input,
                    response=response,
                    status_code=PhaseStatusCode.CONFIRMED,
                )
                self._save_progress(PhaseStatusCode.CONFIRMED)
                
                token_usage = self.agent_manager.get_total_token_usage()
                
                return PhaseResult(
                    status=PhaseStatus.COMPLETED,
                    message=f"Development completed on branch {branch_name} in {self.iteration} iteration(s)",
                    data={
                        "branch": branch_name,
                        "iterations": self.iteration,
                        "final_response": response,
                        "status_code": status_code.value,
                    },
                    token_usage=token_usage,
                )

            elif status_code == PhaseStatusCode.NEED_PERMISSION:
                # Save history BEFORE waiting for user input
                self._save_history(
                    user_input=current_user_input,
                    response=response,
                    status_code=PhaseStatusCode.NEED_PERMISSION,
                    user_response=None,
                )
                self._save_progress(PhaseStatusCode.NEED_PERMISSION)
                
                # Display permission request
                if self.interactive:
                    print(f"\n{'='*60}")
                    print(f"Dev ({self.dev_agent}) - Iteration {self.iteration}:")
                    print(f"{'='*60}")
                    print(response)
                    print(f"{'='*60}\n")
                    print("💡 開發者請求權限。請再次執行 'aaf develop' 來回應。")
                    
                    return PhaseResult(
                        status=PhaseStatus.IN_PROGRESS,
                        message=f"Permission requested in iteration {self.iteration}. Run command again to respond.",
                        data={
                            "iterations": self.iteration,
                            "last_response": response,
                            "status_code": status_code.value,
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
            else:
                # No valid status code found
                self._save_history(
                    user_input=current_user_input,
                    response=response,
                    status_code=PhaseStatusCode.CONFIRMED,  # Default to CONFIRMED
                )
                self._save_progress(PhaseStatusCode.CONFIRMED)
                
                return PhaseResult(
                    status=PhaseStatus.COMPLETED,
                    message=f"Development completed (no explicit status code) in iteration {self.iteration}",
                    data={
                        "branch": branch_name,
                        "iterations": self.iteration,
                        "final_response": response,
                    },
                )

        except KeyboardInterrupt:
            print("\n\n⏸️  Paused by user (Ctrl+C).")
            print(f"💾 Progress saved. Current iteration: {self.iteration}")
            print(f"📝 To resume, run: aaf develop {self.issue_name}")
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
