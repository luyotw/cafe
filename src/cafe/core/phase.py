"""Base class for workflow phases."""

import json
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from cafe.core.status_codes import PhaseStatusCode
from cafe.core.types import PhaseProgress, PhaseResult, PhaseStatus, TokenUsage


class Phase(ABC):
    """Abstract base class for all workflow phases.

    Each phase represents a step in the CAFE workflow (e.g., requirements clarification,
    implementation analysis, development, code review, etc.).

    Subclasses must implement the execute() method to define the phase's behavior.
    
    Attributes:
        interactive: Whether to allow interactive user prompts (default: True)
    """

    def __init__(self, interactive: bool = True, git_ops: Optional["GitOperations"] = None):
        """Initialize phase with common attributes.

        Args:
            interactive: Whether to allow interactive user prompts
            git_ops: Git operations (optional, for automatic issue_dir setup)
        """
        self.interactive = interactive

        # Automatically set issue_dir from current branch if git_ops is provided
        if git_ops is not None:
            self.issue_dir = self._get_issue_dir(git_ops)

    @abstractmethod
    def execute(self) -> PhaseResult:
        """Execute the phase and return the result.

        Returns:
            PhaseResult containing the status and any relevant data

        Raises:
            Any exceptions from phase execution will propagate to the caller
        """
        pass

    def _save_user_input(
        self,
        user_input: str,
        phase_specific_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Save user input at the start of an iteration.

        在每一輪開始時，先儲存 user_input。這樣可以確保：
        1. 即使 agent 執行失敗，user_input 也被記錄了
        2. 下一輪可以從 history 讀取上一輪的 user_input
        3. History 檔案完整記錄每一輪的開始（user input）和結束（agent response）

        Args:
            user_input: 用戶在這一輪開始時的輸入
            phase_specific_data: Phase 特定的初始資料（可選）
        """
        # 確保 history_dir 存在
        if not hasattr(self, "history_dir"):
            raise AttributeError(
                "Phase must have 'history_dir' attribute to use _save_user_input"
            )

        history_dir = Path(self.history_dir)
        history_dir.mkdir(parents=True, exist_ok=True)

        # 確保 iteration 存在
        if not hasattr(self, "iteration"):
            raise AttributeError(
                "Phase must have 'iteration' attribute to use _save_user_input"
            )

        # 建立初始 history data
        history_data: Dict[str, Any] = {
            "iteration": self.iteration,
            "timestamp": datetime.now().isoformat(),
            "user_input": user_input,
        }

        # 加入 phase 特定的初始資料
        if phase_specific_data:
            history_data.update(phase_specific_data)

        # 儲存為 JSON 檔案
        iteration_file = history_dir / f"iteration_{self.iteration:03d}.json"
        with open(iteration_file, "w", encoding="utf-8") as f:
            json.dump(history_data, f, ensure_ascii=False, indent=2)

    def _update_iteration_history(
        self,
        phase_specific_data: Dict[str, Any],
        prompt: Optional[str] = None,
        agent_cli: Optional[str] = None,
        agent_session_id: Optional[str] = None,
        allowed_tools: Optional[List[str]] = None,
        denied_tools: Optional[List[str]] = None,
        cli_command_args: Optional[List[str]] = None,
        status_code: Optional[PhaseStatusCode] = None,
    ) -> None:
        """Update iteration history with agent response and metadata.

        在 agent 回應後，更新已存在的 history 檔案。

        Args:
            phase_specific_data: Phase 特定的資料（如 response 等）
            prompt: Agent 實際收到的 prompt
            agent_cli: Agent 使用的 CLI tool (如 "copilot", "claude")
            agent_session_id: Agent 的 session ID
            allowed_tools: Agent 可使用的 tools 列表
            denied_tools: Agent 不可使用的 tools 列表
            cli_command_args: CLI 命令參數 list（除了 prompt）
            status_code: Phase 狀態碼（如 CONFIRMED, NEED_CLARIFICATION）
        """
        # 確保 history_dir 存在
        if not hasattr(self, "history_dir"):
            raise AttributeError(
                "Phase must have 'history_dir' attribute to use _update_iteration_history"
            )

        history_dir = Path(self.history_dir)
        history_dir.mkdir(parents=True, exist_ok=True)
        iteration_file = history_dir / f"iteration_{self.iteration:03d}.json"

        # 讀取現有的 history data
        if iteration_file.exists():
            with open(iteration_file, "r", encoding="utf-8") as f:
                history_data = json.load(f)
        else:
            # 如果檔案不存在，建立基本結構
            history_data = {
                "iteration": self.iteration,
                "timestamp": datetime.now().isoformat(),
            }

        # 更新 phase 特定資料
        history_data.update(phase_specific_data)

        # 確保必須儲存的欄位存在（如果 phase_specific_data 沒有提供，則設為預設值）
        # 這些欄位在除錯和追蹤時非常重要，應該總是存在
        if "response" not in history_data:
            history_data["response"] = None
        if "permission_denials" not in history_data:
            history_data["permission_denials"] = []
        if "streaming_log" not in history_data:
            history_data["streaming_log"] = []

        # 更新共用的 agent metadata
        history_data["prompt"] = prompt
        history_data["cli"] = agent_cli
        history_data["session_id"] = agent_session_id
        history_data["allowed_tools"] = allowed_tools
        history_data["denied_tools"] = denied_tools
        history_data["cli_command_args"] = cli_command_args
        history_data["status_code"] = status_code.value if status_code is not None else None

        # 儲存更新後的 JSON 檔案
        with open(iteration_file, "w", encoding="utf-8") as f:
            json.dump(history_data, f, ensure_ascii=False, indent=2)

    def _save_iteration_history(
        self,
        phase_specific_data: Dict[str, Any],
        prompt: Optional[str] = None,
        agent_cli: Optional[str] = None,
        agent_session_id: Optional[str] = None,
        allowed_tools: Optional[List[str]] = None,
        denied_tools: Optional[List[str]] = None,
        status_code: Optional[PhaseStatusCode] = None,
    ) -> None:
        """Save iteration history to JSON file (共用方法 - 保留向後兼容).

        此方法保留向後兼容性，直接建立完整的 history 檔案。
        建議新程式碼使用 _save_user_input + _update_iteration_history 的兩階段方式。

        Args:
            phase_specific_data: Phase 特定的資料（如 user_input, response 等）
            prompt: Agent 實際收到的 prompt
            agent_cli: Agent 使用的 CLI tool (如 "copilot", "claude")
            agent_session_id: Agent 的 session ID
            allowed_tools: Agent 可使用的 tools 列表
            denied_tools: Agent 不可使用的 tools 列表
            status_code: Phase 狀態碼（如 CONFIRMED, NEED_CLARIFICATION）
        """
        # 確保 history_dir 存在
        if not hasattr(self, "history_dir"):
            raise AttributeError(
                "Phase must have 'history_dir' attribute to use _save_iteration_history"
            )

        history_dir = Path(self.history_dir)
        history_dir.mkdir(parents=True, exist_ok=True)

        # 確保 iteration 存在
        if not hasattr(self, "iteration"):
            raise AttributeError(
                "Phase must have 'iteration' attribute to use _save_iteration_history"
            )

        # 建立 history data，包含共用欄位和 phase 特定資料
        history_data: Dict[str, Any] = {
            "iteration": self.iteration,
            "timestamp": datetime.now().isoformat(),
        }

        # 加入 phase 特定資料
        history_data.update(phase_specific_data)

        # 加入共用的 agent metadata
        history_data["prompt"] = prompt
        history_data["cli"] = agent_cli
        history_data["session_id"] = agent_session_id
        history_data["allowed_tools"] = allowed_tools
        history_data["denied_tools"] = denied_tools
        history_data["status_code"] = status_code.value if status_code is not None else None

        # 儲存為 JSON 檔案
        iteration_file = history_dir / f"iteration_{self.iteration:03d}.json"
        with open(iteration_file, "w", encoding="utf-8") as f:
            json.dump(history_data, f, ensure_ascii=False, indent=2)

    def _check_empty_response(self, response: str) -> Optional[PhaseStatusCode]:
        """檢查 agent response 是否為空，如果為空返回 NO_RESPONSE 狀態碼。

        這是一個通用的 helper 方法，所有 phases 都可以使用。

        Args:
            response: Agent 的回應內容

        Returns:
            如果 response 為空（空字串或只有空白），返回 PhaseStatusCode.NO_RESPONSE
            否則返回 None
        """
        if not response or not response.strip():
            return PhaseStatusCode.NO_RESPONSE
        return None

    def _execute_agent_iteration(
        self,
        agent_name: str,
        prompt: str,
        user_input: str,
        valid_status_codes: List[PhaseStatusCode],
        allowed_tools: Optional[List[str]] = None,
        denied_tools: Optional[List[str]] = None,
        phase_specific_data: Optional[Dict[str, Any]] = None,
    ) -> tuple[str, Optional[PhaseStatusCode]]:
        """通用的 agent 執行流程，所有 phases 都可以使用。

        此方法封裝了執行 agent 的標準流程：
        1. 保存 user_input 到 history
        2. 獲取 agent metadata
        3. 保存 prompt 到 history
        4. 執行 agent
        5. 檢查空回應
        6. 提取 status code
        7. 更新 history
        8. 保存 progress

        Args:
            agent_name: Agent 名稱（如 pm_agent, dev_agent）
            prompt: 要發送給 agent 的 prompt
            user_input: 用戶在這一輪的輸入
            valid_status_codes: 此 phase 接受的有效 status codes
            allowed_tools: Agent 可使用的 tools（預設為 None）
            denied_tools: Agent 不可使用的 tools（預設為 None）
            phase_specific_data: Phase 特定的初始資料（預設為 None）

        Returns:
            tuple[response, status_code]:
                - response: Agent 的回應內容
                - status_code: 提取的 status code，如果沒有找到則為 None

        Raises:
            AttributeError: 如果 phase 缺少必要的屬性（history_dir, iteration, agent_manager）
        """
        # 檢查必要的屬性
        if not hasattr(self, "history_dir"):
            raise AttributeError("Phase must have 'history_dir' attribute")
        if not hasattr(self, "iteration"):
            raise AttributeError("Phase must have 'iteration' attribute")
        if not hasattr(self, "agent_manager"):
            raise AttributeError("Phase must have 'agent_manager' attribute")

        # 1. 保存 user_input 到 history
        self._save_user_input(
            user_input=user_input,
            phase_specific_data=phase_specific_data or {},
        )

        # 2. 獲取 agent metadata
        agent_executor = self.agent_manager.get_agent(agent_name)
        agent_cli = agent_executor.config.cli.value
        agent_session_id = agent_executor.config.session_id

        # 3. 保存 prompt 到 history（在執行 agent 之前）
        iteration_file = Path(self.history_dir) / f"iteration_{self.iteration:03d}.json"
        if iteration_file.exists():
            with open(iteration_file, "r", encoding="utf-8") as f:
                history_data = json.load(f)
            history_data["prompt"] = prompt
            history_data["cli"] = agent_cli
            history_data["session_id"] = agent_session_id
            history_data["allowed_tools"] = allowed_tools
            history_data["denied_tools"] = denied_tools
            with open(iteration_file, "w", encoding="utf-8") as f:
                json.dump(history_data, f, ensure_ascii=False, indent=2)

        # 4. 執行 agent（with error recovery）
        try:
            response, token_usage, permission_denials, cli_command_args, streaming_log = self.agent_manager.execute(
                agent_name,
                prompt,
                allowed_tools=allowed_tools,
                allowed_directories=self._get_allowed_directories(),
            )
        except Exception as e:
            # Agent execution failed - attempt recovery
            from cafe.agents.executor import AgentExecutionError

            print(f"⚠️  Agent execution failed: {e}")

            # 4a. Check if it's a rate limit error - fail immediately without recovery
            if isinstance(e, AgentExecutionError) and hasattr(e, "error_type") and e.error_type == "rate_limit":
                print(f"❌ Rate limit error detected - stopping execution")

                # Update iteration history with error info
                if iteration_file.exists():
                    with open(iteration_file, "r", encoding="utf-8") as f:
                        history_data = json.load(f)

                    history_data["response"] = None
                    history_data["status_code"] = None
                    history_data["error"] = str(e)
                    history_data["error_type"] = "rate_limit"

                    with open(iteration_file, "w", encoding="utf-8") as f:
                        json.dump(history_data, f, ensure_ascii=False, indent=2)

                # Re-raise immediately without recovery attempt
                raise

            # 4b. 檢查 agent 是否寫入輸出檔案
            written_files = self._detect_written_output_files()

            # 4c. 嘗試從寫入的檔案恢復 response
            recovered_response, recovered_status_code = self._recover_from_written_files(
                written_files,
                valid_status_codes,
            )

            # 4d. 建立 error log 檔案用於除錯
            error_log_file = Path(self.history_dir) / f"execution_error_{self.iteration:03d}.log"
            error_log_file.parent.mkdir(parents=True, exist_ok=True)
            error_log_file.write_text(
                f"Timestamp: {datetime.now().isoformat()}\n"
                f"Error: {str(e)}\n"
                f"Written files: {[str(f) for f in written_files]}\n"
                f"Recovered response: {bool(recovered_response)}\n"
                f"Recovered status: {recovered_status_code.value if recovered_status_code else None}\n",
                encoding="utf-8",
            )

            if recovered_response and recovered_status_code:
                # 4e. 恢復成功 - 視為部分成功
                print(f"✅ Recovered response from {written_files[0].name}")
                print(f"   Status code: {recovered_status_code.value}")

                response = recovered_response
                status_code = recovered_status_code
                permission_denials = []  # 無權限資訊
                token_usage = TokenUsage()  # Empty token usage

                # 加入恢復 metadata 到 iteration history
                phase_specific_data = phase_specific_data or {}
                phase_specific_data["response"] = response
                phase_specific_data["permission_denials"] = []
                phase_specific_data["recovered_from_error"] = True
                phase_specific_data["original_error"] = str(e)

                # 更新 history（包含恢復的 response 和 status）
                # 儘可能記錄實際的 CLI 參數（如果錯誤物件有附帶）
                cli_args: List[str] = []
                if isinstance(e, AgentExecutionError) and hasattr(e, "cli_command_args"):
                    cli_args = getattr(e, "cli_command_args") or []

                self._update_iteration_history(
                    phase_specific_data=phase_specific_data,
                    prompt=prompt,
                    agent_cli=agent_cli,
                    agent_session_id=agent_session_id,
                    allowed_tools=allowed_tools,
                    denied_tools=denied_tools,
                    cli_command_args=cli_args,
                    status_code=status_code,
                )

                # 保存 progress
                if hasattr(self, "_save_progress"):
                    self._save_progress(status_code)

                # 返回恢復的結果
                return response, status_code
            else:
                # 4f. 恢復失敗 - 更新 history 並 re-raise
                print(f"❌ Could not recover from error")

                # 更新 iteration history 包含錯誤資訊與 CLI 參數
                if iteration_file.exists():
                    with open(iteration_file, "r", encoding="utf-8") as f:
                        history_data = json.load(f)

                    history_data["response"] = None
                    history_data["status_code"] = None
                    history_data["error"] = str(e)

                    # 記錄錯誤類型（如果有）
                    if isinstance(e, AgentExecutionError) and hasattr(e, "error_type"):
                        history_data["error_type"] = e.error_type

                    # 儘可能記錄實際使用的 CLI 命令參數
                    if isinstance(e, AgentExecutionError) and hasattr(e, "cli_command_args"):
                        history_data["cli_command_args"] = getattr(e, "cli_command_args")
                    else:
                        # 明確標記為 None，方便後續偵錯時看出「沒有可用的 CLI 參數」
                        history_data.setdefault("cli_command_args", None)

                    with open(iteration_file, "w", encoding="utf-8") as f:
                        json.dump(history_data, f, ensure_ascii=False, indent=2)

                # Re-raise 讓 phase 處理失敗
                raise

        # 5. 檢查空回應
        no_response_status = self._check_empty_response(response)
        if no_response_status:
            # Agent 返回空回應 - 保存並返回 NO_RESPONSE
            self._update_iteration_history(
                phase_specific_data={
                    "response": response,
                    "permission_denials": [denial.model_dump() for denial in permission_denials],
                    "streaming_log": streaming_log
                },
                prompt=prompt,
                agent_cli=agent_cli,
                agent_session_id=agent_session_id,
                allowed_tools=allowed_tools,
                denied_tools=denied_tools,
                cli_command_args=cli_command_args,
                status_code=no_response_status,
            )
            return response, no_response_status

        # 6. 提取 status code
        from cafe.core.status_codes import StatusCodeParser
        status_code = StatusCodeParser.extract(
            response,
            valid_codes=valid_status_codes,
        )

        # 6.1. 檢查是否有多個不同的 status codes
        all_status_codes = StatusCodeParser.extract_all(response, valid_codes=valid_status_codes)
        has_multiple_codes = len(all_status_codes) > 1

        # 6.2. 如果沒有 status code（包含多個 status codes 的情況），發送繼續訊息
        # 因為多個 status codes 也代表狀態不明確，需要 agent 確認
        analysis_attempted = False
        analysis_response = None
        original_status_code_missing = (status_code is None)  # 記錄原始狀態

        if original_status_code_missing:  # 包含：沒有 status code 或有多個 status codes
            print(f"\n⚠️  Agent 回應沒有狀態碼，發送繼續訊息...")
            analysis_attempted = True
            try:
                # 發送繼續提示，並附加原本的 prompt 作為參考
                # 這樣即使 session 被重新建立，agent 也能理解完整的 context
                continue_prompt = f"若完成了就回應狀態碼，未完成就繼續\n\n以下是原本的任務說明供參考：\n\n{prompt}"
                continue_response, _, _, _, _ = self.agent_manager.execute(
                    agent_name,
                    continue_prompt,
                    allowed_tools=allowed_tools,
                    allowed_directories=self._get_allowed_directories(),
                )

                # 嘗試從繼續的回應中提取狀態碼
                continue_status_code = StatusCodeParser.extract(
                    continue_response,
                    valid_codes=valid_status_codes,
                )

                if continue_status_code:
                    print(f"✅ 繼續執行後獲得狀態碼: {continue_status_code.value}")
                    # 將原始回應和繼續回應合併
                    response = response + "\n\n" + continue_response
                    status_code = continue_status_code
                    analysis_response = continue_response
                else:
                    print(f"⚠️  繼續執行後仍未獲得狀態碼")
                    analysis_response = continue_response

            except Exception as e:
                print(f"⚠️  繼續訊息發送失敗: {e}")
                analysis_response = None

        # 6.3. 寫入 error log（如果原始回應有問題：沒有 status code、多個 codes、或分析後仍無 status code）
        # 注意：即使分析成功找到 status code，我們仍然記錄原始回應的問題
        if analysis_attempted or original_status_code_missing:
            self._write_status_code_error_log(
                original_response=response,
                valid_status_codes=valid_status_codes,
                status_code=status_code,
                analysis_attempted=analysis_attempted,
                analysis_response=analysis_response,
                cli_command_args=cli_command_args,
                multiple_codes_found=list(all_status_codes) if has_multiple_codes else None,
            )

        # 7. 更新 history（總是保存，即使沒有 status code）
        phase_data = {
            "response": response,
            "permission_denials": [denial.model_dump() for denial in permission_denials],
            "streaming_log": streaming_log
        }
        
        # 如果進行了 status code 分析，記錄到 history
        if analysis_attempted:
            phase_data["status_code_analyzed"] = True
            if analysis_response:
                phase_data["status_code_analysis_response"] = analysis_response
        
        self._update_iteration_history(
            phase_specific_data=phase_data,
            prompt=prompt,
            agent_cli=agent_cli,
            agent_session_id=agent_session_id,
            allowed_tools=allowed_tools,
            denied_tools=denied_tools,
            cli_command_args=cli_command_args,
            status_code=status_code,
        )

        # 8. 保存 progress（如果有 status code 且 phase 有 _save_progress 方法）
        if status_code and hasattr(self, "_save_progress"):
            self._save_progress(status_code)  # type: ignore

        return response, status_code

    def _ask_user_for_review_decision(self, item_name: str = "內容") -> str:
        """詢問用戶對 READY_FOR_REVIEW 的決定（interactive 模式）。

        Args:
            item_name: 要確認的項目名稱（如「計畫」、「程式碼」、「需求」）

        Returns:
            str: "confirm", "reject", 或修改意見內容
        """
        # Use phase's display if available, otherwise create new one
        if hasattr(self, 'display'):
            display = self.display  # type: ignore
        else:
            from cafe.ui.display import Display
            display = Display()

        print(f"開發者認為{item_name}已完成。請確認：")
        print("  [c] confirm - 確認，繼續")
        print("  [r] reject - 拒絕，終止")
        print("  [m] modify - 要求修改（輸入修改意見）")

        while True:
            choice = input("\n請選擇 [c/r/m]: ").strip().lower()

            if choice == 'c':
                return "confirm"
            elif choice == 'r':
                return "reject"
            elif choice == 'm':
                modification_request = display.get_multiline_input("請輸入修改意見")

                if not modification_request.strip():
                    print("\n⚠️  沒有輸入修改意見，請重新選擇。")
                    continue

                print()
                print("✅ 已收到您的修改意見...")
                print()

                return modification_request
            else:
                print("❌ 無效選擇，請輸入 c, r, 或 m")

    def _ask_user_for_clarification(self) -> str:
        """詢問用戶對 NEED_CLARIFICATION 的回答（interactive 模式）。

        Returns:
            str: 用戶的回答
        """
        # Use phase's display if available, otherwise create new one
        if hasattr(self, 'display'):
            display = self.display  # type: ignore
        else:
            from cafe.ui.display import Display
            display = Display()

        return display.get_multiline_input("請回答問題")

    def _process_review_decision(
        self,
        choice: str,
        prev_data: Dict[str, Any],
        phase_name: str,
        phase_specific_data: Optional[Dict[str, Any]] = None,
    ) -> "PhaseResult | str":
        """處理用戶對 READY_FOR_REVIEW 的決定。

        Args:
            choice: "confirm", "reject", 或修改意見內容
            prev_data: 上一輪的 iteration data
            phase_name: Phase 名稱（用於訊息，如 "Implementation plan", "Requirements"）
            phase_specific_data: Phase 特定的資料（用於保存 history）

        Returns:
            PhaseResult: 如果 confirm 或 reject
            str: 如果要求修改，返回修改意見
        """
        if choice == "confirm":
            # Save user confirmation as a new iteration
            self._save_user_input(
                user_input="confirm",
                phase_specific_data=phase_specific_data or {},
            )
            self._update_iteration_history(
                phase_specific_data={
                    "response": "User confirmed",
                    "user_action": "confirm",
                },
                prompt="",
                agent_cli=None,
                agent_session_id=None,
                allowed_tools=None,
                status_code=PhaseStatusCode.CONFIRMED,
            )
            self._save_progress(PhaseStatusCode.CONFIRMED)

            return PhaseResult(
                status=PhaseStatus.COMPLETED,
                message=f"{phase_name} completed in {self.iteration} iteration(s)",
                data={
                    "iterations": self.iteration,
                    "final_response": prev_data.get("response", ""),
                    "status_code": PhaseStatusCode.CONFIRMED.value,
                },
            )
        elif choice == "reject":
            return PhaseResult(
                status=PhaseStatus.FAILED,
                message=f"{phase_name} rejected by user in iteration {self.iteration - 1}",
                data={
                    "iterations": self.iteration - 1,
                    "final_response": prev_data.get("response", ""),
                    "status_code": "USER_REJECTED",
                },
            )
        else:
            # choice is the modification request
            return choice

    def _get_status_file(self) -> Path:
        """Get the path to status.json file.

        Returns:
            Path to status.json in {history_dir.parent}/status.json

        For different phases:
            - SpecPhase: .cafe/issues/myissue/spec/status.json
            - PlanPhase: .cafe/issues/myissue/plan/status.json
            - DevelopPhase: .cafe/issues/myissue/develop/status.json
        """
        if not hasattr(self, "history_dir"):
            raise AttributeError("Phase must have 'history_dir' attribute")
        return Path(self.history_dir).parent / "status.json"

    def _detect_written_output_files(self) -> List[Path]:
        """偵測 agent 在失敗前是否已寫入輸出檔案。

        Base implementation 返回空列表（不進行 recovery）。
        子類應覆寫此方法以檢查 phase-specific 的輸出檔案。

        Returns:
            List[Path]: Agent 寫入的檔案路徑列表
        """
        return []

    def _recover_from_written_files(
        self,
        written_files: List[Path],
        valid_status_codes: List[PhaseStatusCode],
    ) -> tuple[Optional[str], Optional[PhaseStatusCode]]:
        """嘗試從已寫入的檔案中恢復 agent response。

        Args:
            written_files: Agent 在失敗前寫入的檔案列表
            valid_status_codes: 此 phase 有效的 status codes

        Returns:
            Tuple[Optional[str], Optional[PhaseStatusCode]]:
                - recovered_response: 恢復的 response 內容
                - extracted_status_code: 從檔案中提取的 status code
                如果恢復失敗則返回 (None, None)
        """
        if not written_files:
            return None, None

        # 讀取第一個（主要）輸出檔案
        try:
            primary_file = written_files[0]
            recovered_response = primary_file.read_text(encoding="utf-8")

            # 從檔案內容提取 status code
            from cafe.core.status_codes import StatusCodeParser
            status_code = StatusCodeParser.extract(
                recovered_response,
                valid_codes=valid_status_codes,
            )

            return recovered_response, status_code
        except Exception as e:
            # 記錄恢復失敗但不 raise
            print(f"⚠️  Failed to recover from written files: {e}")
            return None, None

    def _save_progress(self, status_code: PhaseStatusCode) -> None:
        """Save phase progress to status.json（通用方法）。

        Args:
            status_code: Phase status code (CONFIRMED, READY_FOR_REVIEW, NEED_CLARIFICATION, etc.)
        """
        if not hasattr(self, "iteration"):
            raise AttributeError("Phase must have 'iteration' attribute")
        if not hasattr(self, "phase_name"):
            raise AttributeError("Phase must have 'phase_name' attribute (e.g., 'spec', 'plan')")

        status_file = self._get_status_file()
        status_file.parent.mkdir(parents=True, exist_ok=True)

        # Determine phase status based on status code
        complete_codes = [PhaseStatusCode.CONFIRMED, PhaseStatusCode.READY_FOR_REVIEW]
        phase_status = PhaseStatus.COMPLETED if status_code in complete_codes else PhaseStatus.IN_PROGRESS

        progress = PhaseProgress(
            phase=self.phase_name,
            status=phase_status,
            status_code=status_code.value,
            timestamp=datetime.now(),
            iteration=self.iteration,
            message=f"Phase completed with {status_code.value}" if phase_status == PhaseStatus.COMPLETED else f"Iteration {self.iteration}",
        )

        with open(status_file, 'w', encoding='utf-8') as f:
            json.dump(progress.to_dict(), f, ensure_ascii=False, indent=2)

    def _load_progress(self) -> Optional["PhaseProgress"]:
        """Load phase progress from status.json（通用方法）。

        Returns:
            PhaseProgress if file exists, None otherwise
        """
        status_file = self._get_status_file()
        if not status_file.exists():
            return None

        with open(status_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        from cafe.core.types import PhaseProgress
        return PhaseProgress.from_dict(data)

    def _print_token_usage_summary(self) -> None:
        """顯示 token usage 摘要（通用方法）。

        此方法會從 agent_manager 取得總 token usage 並顯示摘要。
        適用於 phase 完成時顯示成本統計。
        """
        if not hasattr(self, "agent_manager"):
            return

        token_usage = self.agent_manager.get_total_token_usage()

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

    def _handle_need_clarification_input(
        self,
        prev_data: dict,
        agent_display_name: str = "agent",
    ) -> Any:
        """處理 NEED_CLARIFICATION 狀態的用戶輸入（通用方法）。

        此方法封裝了 NEED_CLARIFICATION 狀態的標準處理流程：
        1. Interactive 模式：呼叫 _ask_user_for_clarification() 提示用戶輸入
        2. Non-interactive 模式：使用 self.user_input（用完後清空）
        3. 驗證用戶輸入不為空
        4. 返回用戶輸入或 PhaseResult（錯誤狀態）

        Args:
            prev_data: 上一輪 iteration 的資料（從 history JSON 讀取）
            agent_display_name: Agent 顯示名稱（如 "PM"、"開發者"），用於確認訊息

        Returns:
            str: 用戶輸入的 clarification
            PhaseResult: 如果失敗（FAILED）
        """
        # 檢查必要的屬性
        if not hasattr(self, "iteration"):
            raise AttributeError("Phase must have 'iteration' attribute")
        if not hasattr(self, "interactive"):
            raise AttributeError("Phase must have 'interactive' attribute")
        if not hasattr(self, "user_input"):
            raise AttributeError("Phase must have 'user_input' attribute")

        # 需要用戶回答問題
        if self.interactive:
            if not hasattr(self, "_ask_user_for_clarification"):
                raise AttributeError("Phase must implement '_ask_user_for_clarification' method")
            clarification = self._ask_user_for_clarification()
        else:
            clarification = self.user_input
            # Non-interactive 模式：用完後清空，確保不重複使用
            self.user_input = ""
            
            if not clarification:
                # Non-interactive 但沒提供輸入 → 立即失敗
                return PhaseResult(
                    status=PhaseStatus.FAILED,
                    message=f"{self.phase_name.capitalize()} phase failed after iteration {self.iteration - 1}: received NEED_CLARIFICATION in non-interactive mode without user input",
                    data={
                        "iterations": self.iteration - 1,
                        "last_response": prev_data.get("response", ""),
                        "status_code": "CAFE_NEED_CLARIFICATION",
                    },
                )

        # 處理用戶回答（不再區分 interactive/non-interactive）
        if not clarification.strip():
            return PhaseResult(
                status=PhaseStatus.FAILED,
                message="User provided no response to clarification questions",
                data={
                    "iterations": self.iteration - 1,
                    "last_response": prev_data.get("response", ""),
                },
            )

        if self.interactive:
            print()
            print(f"✅ 已收到您的回答，正在發送給{agent_display_name}處理...")
            print()

        return clarification

    def _load_previous_iteration_data(self) -> Optional[dict]:
        """載入上一輪 iteration 的資料（通用方法）。

        Returns:
            dict: 上一輪 iteration 的資料，如果不存在則返回 None
        """
        if not hasattr(self, "iteration"):
            raise AttributeError("Phase must have 'iteration' attribute")
        if not hasattr(self, "history_dir"):
            raise AttributeError("Phase must have 'history_dir' attribute")

        if self.iteration == 1:
            return None

        prev_iteration_file = Path(self.history_dir) / f"iteration_{self.iteration - 1:03d}.json"
        if not prev_iteration_file.exists():
            return None

        with open(prev_iteration_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def _merge_allowed_tools(
        self,
        base_allowed_tools: List[str],
        approved_tools_from_denials: Optional[List[str]] = None,
    ) -> List[str]:
        """合併 base allowed tools 和前一輪的 allowed tools（通用方法）。

        這個方法會：
        1. 從前一輪 iteration 讀取 allowed_tools
        2. 合併 base_allowed_tools + 前一輪的 allowed_tools + 新批准的 tools
        3. 去除重複項目

        Args:
            base_allowed_tools: 此 phase 的基礎工具列表
            approved_tools_from_denials: 從這一輪 permission denials 新批准的工具（預設為空列表）

        Returns:
            合併且去重後的工具列表
        """
        approved_tools_from_denials = approved_tools_from_denials or []

        # Load previous iteration's allowed tools
        prev_allowed_tools: List[str] = []
        prev_data = self._load_previous_iteration_data()
        if prev_data and prev_data.get("allowed_tools"):
            prev_allowed_tools = prev_data.get("allowed_tools", [])

        # Merge: base tools + previous iteration's tools + newly approved tools from denials
        # Use set to avoid duplicates, then convert back to list
        return list(set(base_allowed_tools + prev_allowed_tools + approved_tools_from_denials))

    def _get_allowed_directories(self) -> List[str]:
        """取得允許的目錄列表。

        回傳需要讓底層 CLI 工具存取的目錄列表。
        預設回傳 [".cafe"]，讓所有 CLI 工具都能讀取 .cafe 目錄。

        Returns:
            允許的目錄列表
        """
        return [".cafe"]

    def _handle_previous_permission_denials(self) -> tuple[List[str], str]:
        """處理上一輪的 permission denials，返回批准的工具和用戶輸入。

        這個方法處理兩種模式：
        1. Interactive 模式：逐一詢問用戶是否批准每個被拒絕的工具
        2. Non-interactive 模式：使用 self.approved_denial_indices 批准工具

        Returns:
            tuple[List[str], str]: (批准的工具列表, 用戶關於權限的額外說明)
        """
        from cafe.core.types import PermissionDenial

        # 載入上一輪的 iteration 資料
        prev_data = self._load_previous_iteration_data()
        if not prev_data:
            return ([], "")

        # 檢查是否有 permission_denials
        permission_denials_data = prev_data.get("permission_denials", [])
        if not permission_denials_data:
            return ([], "")

        # 將 dict 轉換為 PermissionDenial 物件
        permission_denials = [
            PermissionDenial(**denial_data) for denial_data in permission_denials_data
        ]

        # 收集被批准的 indices
        approved_indices: List[int] = []

        if self.interactive:
            # Interactive 模式：先顯示所有權限請求，然後逐一詢問
            print("\n上一輪執行中有以下權限請求被拒絕：\n")
            for idx, denial in enumerate(permission_denials):
                # 顯示工具名稱和主要參數
                if "file_path" in denial.tool_input:
                    print(f"[{idx}] {denial.tool_name}({denial.tool_input['file_path']})")
                elif "command" in denial.tool_input:
                    print(f"[{idx}] {denial.tool_name}({denial.tool_input['command']})")
                else:
                    print(f"[{idx}] {denial.tool_name}(...)")

            print()  # 空行分隔

            # 逐一詢問每個權限
            for idx, denial in enumerate(permission_denials):
                # 顯示要授權的工具
                if "file_path" in denial.tool_input:
                    tool_desc = f"{denial.tool_name}({denial.tool_input['file_path']})"
                elif "command" in denial.tool_input:
                    tool_desc = f"{denial.tool_name}({denial.tool_input['command']})"
                else:
                    tool_desc = f"{denial.tool_name}"

                response = input(f"是否批准 [{idx}] {tool_desc}？(y/n): ").strip().lower()
                if response == 'y':
                    approved_indices.append(idx)

            # 詢問用戶是否有額外說明
            print()
            user_input = self.display.get_multiline_input(
                "關於這些權限，是否有額外的說明或指示要給 agent？（可留空）"
            )
        else:
            # Non-interactive 模式：使用預設的 approved_denial_indices
            if not hasattr(self, "approved_denial_indices"):
                raise AttributeError("Phase must have 'approved_denial_indices' attribute in non-interactive mode")
            if not hasattr(self, "user_input"):
                raise AttributeError("Phase must have 'user_input' attribute in non-interactive mode")

            approved_indices = self.approved_denial_indices
            user_input = self.user_input

        # 將批准的 indices 轉換為 allowed_tools 格式
        approved_tools: List[str] = []
        for idx in approved_indices:
            if idx < len(permission_denials):
                denial = permission_denials[idx]
                approved_tools.append(denial.to_allowed_tool_pattern())

        return (approved_tools, user_input)

    def _load_current_iteration_data(self) -> Optional[dict]:
        """載入當前 iteration 的資料（通用方法）。

        用於恢復被中斷的 iteration（有 user_input 但沒有 response）。

        Returns:
            dict: 當前 iteration 的資料，如果不存在則返回 None
        """
        if not hasattr(self, "iteration"):
            raise AttributeError("Phase must have 'iteration' attribute")
        if not hasattr(self, "history_dir"):
            raise AttributeError("Phase must have 'history_dir' attribute")

        current_iteration_file = Path(self.history_dir) / f"iteration_{self.iteration:03d}.json"
        if not current_iteration_file.exists():
            return None

        with open(current_iteration_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def _load_iteration_counter(self) -> int:
        """從 history 檔案載入最新的 iteration 數字（通用方法）。

        如果最後一個 iteration 沒有 response（被中斷），則返回前一個完整 iteration 的數字，
        這樣下次執行時會重用被中斷的 iteration 編號。

        Returns:
            最新完整 iteration 的數字，如果沒有 history 則返回 0
        """
        if not hasattr(self, "history_dir"):
            raise AttributeError("Phase must have 'history_dir' attribute")

        history_dir = Path(self.history_dir)
        if not history_dir.exists():
            return 0

        iteration_files = sorted(history_dir.glob("iteration_*.json"))
        if not iteration_files:
            return 0

        # 從後往前尋找第一個完整的 iteration（有 response）
        for iteration_file in reversed(iteration_files):
            with open(iteration_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            # 檢查是否有 response（完整的 iteration）
            if "response" in data and data["response"]:
                return data.get("iteration", 0)

        # 所有 iteration 都不完整，返回 0
        return 0

    def _check_if_already_completed(
        self,
        complete_status_codes: List[PhaseStatusCode],
        force: bool = False,
    ) -> Optional[PhaseResult]:
        """檢查 phase 是否已經完成（通用方法）。

        如果 status.json 顯示 phase 已完成且狀態碼在 complete_status_codes 中，
        則返回 COMPLETED 結果，否則返回 None。

        Args:
            complete_status_codes: 表示完成的狀態碼列表（如 [CONFIRMED] 或 [READY_FOR_REVIEW]）
            force: 如果為 True，則跳過已完成檢查，允許重新執行（預設：False）

        Returns:
            PhaseResult 如果已完成，None 如果未完成
        """
        # If force is True, skip the check and allow re-execution
        if force:
            return None

        if not hasattr(self, "agent_manager"):
            raise AttributeError("Phase must have 'agent_manager' attribute")

        status_file = self._get_status_file()
        if not status_file.exists():
            return None

        with open(status_file, "r", encoding="utf-8") as f:
            status_data = json.load(f)

        # 檢查是否已完成且狀態碼符合
        if status_data.get("status") != "completed":
            return None

        status_code_value = status_data.get("status_code", "")
        for complete_code in complete_status_codes:
            if status_code_value == complete_code.value:
                # 已完成，返回結果
                token_usage = self.agent_manager.get_total_token_usage()
                return PhaseResult(
                    status=PhaseStatus.COMPLETED,
                    message=f"Phase already completed with status {status_code_value}",
                    data={
                        "iterations": status_data.get("iteration", 0),
                        "status_code": status_code_value,
                        "token_usage": {
                            "input_tokens": token_usage.input_tokens,
                            "output_tokens": token_usage.output_tokens,
                            "cache_creation_input_tokens": token_usage.cache_creation_input_tokens,
                            "cache_read_input_tokens": token_usage.cache_read_input_tokens,
                            "total_cost_usd": token_usage.total_cost_usd,
                        },
                    },
                )

        return None

    def _check_max_iterations(
        self,
        max_iterations: int,
        phase_name: str = "Phase",
    ) -> Optional[PhaseResult]:
        """檢查是否超過最大迭代次數（通用方法）。

        Args:
            max_iterations: 最大迭代次數
            phase_name: Phase 名稱（用於錯誤訊息）

        Returns:
            PhaseResult 如果超過最大次數，None 如果未超過
        """
        if not hasattr(self, "iteration"):
            raise AttributeError("Phase must have 'iteration' attribute")

        if self.iteration > max_iterations:
            return PhaseResult(
                status=PhaseStatus.FAILED,
                message=f"{phase_name} exceeded maximum iterations ({max_iterations}). Did not converge.",
                data={
                    "iterations": self.iteration - 1,
                    "max_iterations": max_iterations,
                },
            )

        return None

    def _execute_and_handle_agent_response(
        self,
        agent_name: str,
        user_input: str,
        valid_status_codes: List[PhaseStatusCode],
        allowed_tools: Optional[List[str]] = None,
        continue_codes: Optional[List[PhaseStatusCode]] = None,
        complete_codes: Optional[List[PhaseStatusCode]] = None,
        phase_specific_data: Optional[Dict[str, Any]] = None,
    ) -> tuple[Optional[PhaseResult], str]:
        """執行完整的 agent 互動循環：生成 prompt、執行 agent、處理 status code（通用方法）。

        此方法封裝了標準的 agent 執行流程，供所有 phases 使用。
        Phase-specific 邏輯應該透過：
        1. _generate_prompt() - 生成 phase-specific 的 prompt
        2. _get_completion_data() - 提供 phase-specific 的完成資料
        3. 在呼叫後執行 phase-specific 的後處理（如 sync to GitHub）

        Args:
            agent_name: Agent 名稱
            user_input: 用戶在這一輪的輸入
            valid_status_codes: 有效的狀態碼列表
            allowed_tools: 允許的工具列表
            continue_codes: 應該繼續循環的 status codes
            complete_codes: 表示即將完成的 status codes
            phase_specific_data: Phase-specific 資料（傳給 _execute_agent_iteration）

        Returns:
            (PhaseResult 如果應該結束 phase，None 如果應該繼續下一輪循環, agent response)
        """
        # Generate prompt for this iteration (subclass implements this)
        if not hasattr(self, '_generate_prompt'):
            raise AttributeError("Phase must implement '_generate_prompt' method")

        # Call _generate_prompt with or without user_input parameter
        # Try with user_input first, fall back to no parameter
        import inspect
        sig = inspect.signature(self._generate_prompt)
        if len(sig.parameters) > 0:
            prompt = self._generate_prompt(user_input)
        else:
            prompt = self._generate_prompt()

        # Execute agent iteration using common method
        response, status_code = self._execute_agent_iteration(
            agent_name=agent_name,
            prompt=prompt,
            user_input=user_input,
            valid_status_codes=valid_status_codes,
            allowed_tools=allowed_tools or ["write", "read"],
            phase_specific_data=phase_specific_data,
        )

        # Use base class method to handle standard status codes
        result = self._handle_standard_status_codes(
            status_code=status_code,
            response=response,
            complete_codes=complete_codes or [],
            continue_codes=continue_codes or [],
        )

        return result, response

    def _handle_standard_status_codes(
        self,
        status_code: Optional[PhaseStatusCode],
        response: str,
        continue_codes: Optional[List[PhaseStatusCode]] = None,
        complete_codes: Optional[List[PhaseStatusCode]] = None,
    ) -> Optional[PhaseResult]:
        """處理標準的 status codes，返回 PhaseResult 或 None（表示繼續循環）。

        此方法封裝了常見的 status code 處理邏輯：
        - NO_RESPONSE: 返回 FAILED
        - REJECTED: 返回 FAILED
        - continue_codes 中的 codes: 返回 None（繼續循環）
        - complete_codes 中的 codes: 返回 None（繼續循環，但通常會在下一輪處理完成邏輯）
        - None (沒有 status code): interactive 模式返回 None，non-interactive 返回 IN_PROGRESS

        Args:
            status_code: 從 agent 回應中提取的 status code
            response: Agent 的回應內容
            continue_codes: 應該繼續循環的 status codes（如 NEED_CLARIFICATION）
            complete_codes: 表示即將完成的 status codes（如 READY_FOR_REVIEW, CONFIRMED）

        Returns:
            PhaseResult 如果應該結束 phase，None 如果應該繼續下一輪循環
        """
        # 檢查必要的屬性
        if not hasattr(self, "iteration"):
            raise AttributeError("Phase must have 'iteration' attribute")
        if not hasattr(self, "interactive"):
            raise AttributeError("Phase must have 'interactive' attribute")

        continue_codes = continue_codes or []
        complete_codes = complete_codes or []

        # Handle NO_RESPONSE
        if status_code == PhaseStatusCode.NO_RESPONSE:
            return PhaseResult(
                status=PhaseStatus.FAILED,
                message=f"Agent returned no response in iteration {self.iteration}",
                data={
                    "iterations": self.iteration,
                    "status_code": status_code.value,
                },
            )

        # Handle REJECTED
        if status_code == PhaseStatusCode.REJECTED:
            result_data = {
                "iterations": self.iteration,
                "final_response": response,
                "status_code": status_code.value,
            }

            # Allow phases to add phase-specific data (e.g., spec_file)
            if hasattr(self, '_get_completion_data'):
                phase_data = self._get_completion_data()
                result_data.update(phase_data)

            return PhaseResult(
                status=PhaseStatus.FAILED,
                message=f"Phase rejected in iteration {self.iteration}",
                data=result_data,
            )

        # Handle complete codes (e.g., CONFIRMED)
        # For CONFIRMED status, print token usage and return completed result
        if status_code == PhaseStatusCode.CONFIRMED:
            self._print_token_usage_summary()

            token_usage = self.agent_manager.get_total_token_usage()
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

            # Allow phases to add phase-specific data
            if hasattr(self, '_get_completion_data'):
                phase_data = self._get_completion_data()
                result_data.update(phase_data)

            return PhaseResult(
                status=PhaseStatus.COMPLETED,
                message=f"Phase completed in {self.iteration} iteration(s)",
                data=result_data,
                token_usage=token_usage,
            )

        # Handle other complete codes (e.g., READY_FOR_REVIEW)
        # Both interactive and non-interactive: complete immediately
        # If user wants to continue, they can run the command again manually
        if status_code in complete_codes:
            self._print_token_usage_summary()
            token_usage = self.agent_manager.get_total_token_usage()
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
            if hasattr(self, '_get_completion_data'):
                phase_data = self._get_completion_data()
                result_data.update(phase_data)

            return PhaseResult(
                status=PhaseStatus.COMPLETED,
                message=f"Phase completed in {self.iteration} iteration(s)",
                data=result_data,
                token_usage=token_usage,
            )

        # Handle continue codes (e.g., NEED_CLARIFICATION)
        # After removing while loops: complete immediately, user runs command again manually if needed
        if status_code in continue_codes:
            self._print_token_usage_summary()
            token_usage = self.agent_manager.get_total_token_usage()
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
            if hasattr(self, '_get_completion_data'):
                phase_data = self._get_completion_data()
                result_data.update(phase_data)

            return PhaseResult(
                status=PhaseStatus.COMPLETED,
                message=f"Phase completed in {self.iteration} iteration(s) - {status_code.value}",
                data=result_data,
                token_usage=token_usage,
            )

        # Handle no status code found
        if status_code is None:
            if self.interactive:
                # Interactive mode: continue iteration
                return None
            else:
                # Non-interactive mode: exit and wait for next call
                return PhaseResult(
                    status=PhaseStatus.IN_PROGRESS,
                    message=f"Iteration {self.iteration}: No status code found, need more iterations",
                    data={
                        "iterations": self.iteration,
                        "status_code": None,
                    },
                )

        # Unknown status code - continue
        return None

    def _analyze_missing_status_code(
        self,
        agent_name: str,
        valid_status_codes: List[PhaseStatusCode],
    ) -> Optional[PhaseStatusCode]:
        """當 response 沒有 status code 時，呼叫 agent 分析狀態。

        此方法用於處理某些 agent 不在 response 中回傳 status code 的情況。
        它會使用 phase-specific 的 prompt 來請 agent 分析目前的狀態。

        Args:
            agent_name: Agent 名稱
            valid_status_codes: 此 phase 有效的 status codes

        Returns:
            分析出的 PhaseStatusCode，如果無法分析則返回 None
        """
        # 取得 phase-specific 的分析 prompt
        prompt = self._get_status_analysis_prompt()
        if not prompt:
            return None

        # 呼叫 agent 分析狀態（只需要 response）
        response, _, _, _, _ = self.agent_manager.execute(
            agent_name, prompt, allowed_directories=self._get_allowed_directories()
        )

        # 從回應中提取 status code
        from cafe.core.status_codes import StatusCodeParser
        return StatusCodeParser.extract(response, valid_codes=valid_status_codes)

    def _analyze_missing_status_code_with_logging(
        self,
        agent_name: str,
        valid_status_codes: List[PhaseStatusCode],
        original_response: str,
    ) -> tuple[Optional[str], Optional[PhaseStatusCode]]:
        """當 response 沒有 status code 時，呼叫 agent 分析狀態（帶日誌）。

        此方法是 _analyze_missing_status_code 的加強版本，會返回分析 response 以便記錄。

        Args:
            agent_name: Agent 名稱
            valid_status_codes: 此 phase 有效的 status codes
            original_response: 原始回應（用於日誌）

        Returns:
            tuple[analysis_response, status_code]:
                - analysis_response: 分析呼叫的回應內容（可能為 None）
                - status_code: 提取的 status code（可能為 None）
        """
        # 取得 phase-specific 的分析 prompt
        prompt = self._get_status_analysis_prompt()
        if not prompt:
            return None, None

        try:
            # 呼叫 agent 分析狀態（需要 read 權限）
            allowed_tools = ["read", "grep", "glob", "ls"]
            response, _, _, _, _ = self.agent_manager.execute(
                agent_name,
                prompt,
                allowed_tools=allowed_tools,
                allowed_directories=self._get_allowed_directories()
            )

            # 從回應中提取 status code
            from cafe.core.status_codes import StatusCodeParser
            status_code = StatusCodeParser.extract(response, valid_codes=valid_status_codes)

            return response, status_code
        except Exception as e:
            # 分析失敗，返回 None
            print(f"⚠️  Status code analysis failed: {e}")
            return None, None

    def _write_status_code_error_log(
        self,
        original_response: str,
        valid_status_codes: List[PhaseStatusCode],
        status_code: Optional[PhaseStatusCode],
        analysis_attempted: bool,
        analysis_response: Optional[str],
        cli_command_args: Optional[List[str]],
        multiple_codes_found: Optional[List[PhaseStatusCode]] = None,
    ) -> None:
        """寫入 status code 錯誤日誌到 execution_error_{num}.log。

        當遇到以下情況時寫入日誌：
        - 原始回應沒有 status code
        - 原始回應有多個 status code
        - 分析嘗試失敗

        Args:
            original_response: 原始 agent 回應
            valid_status_codes: 有效的 status codes
            status_code: 最終提取的 status code（可能為 None）
            analysis_attempted: 是否嘗試了分析
            analysis_response: 分析呼叫的回應（如果有）
            cli_command_args: CLI 命令參數
            multiple_codes_found: 如果發現多個 status codes，列出所有找到的 codes
        """
        if not hasattr(self, "history_dir") or not hasattr(self, "iteration"):
            return

        # 只在需要時寫入 log：沒有 status code、進行了分析、或發現多個 codes
        if status_code is not None and not analysis_attempted and not multiple_codes_found:
            return

        error_log_file = Path(self.history_dir) / f"execution_error_{self.iteration:03d}.log"
        error_log_file.parent.mkdir(parents=True, exist_ok=True)

        # 決定錯誤類型
        if multiple_codes_found:
            error_type = "Multiple status codes"
        elif status_code is None:
            error_type = "Missing status code"
        else:
            error_type = "Status code required analysis"

        # 建立日誌內容
        log_lines = [
            f"Timestamp: {datetime.now().isoformat()}",
            f"Issue: {self.issue_dir.name if hasattr(self, 'issue_dir') else 'unknown'}",
            f"Iteration: {self.iteration}",
            f"Phase: {self.phase_name if hasattr(self, 'phase_name') else 'unknown'}",
            f"Error type: {error_type}",
            "",
            "Original response:",
            "-" * 60,
            original_response,
            "-" * 60,
            "",
            f"Valid status codes: {[code.value for code in valid_status_codes]}",
            f"Extracted status code: {status_code.value if status_code else None}",
        ]

        if multiple_codes_found:
            log_lines.extend([
                "",
                f"Multiple status codes found: {[code.value for code in multiple_codes_found]}",
            ])
        
        log_lines.append("")
        log_lines.append(f"Analysis attempted: {analysis_attempted}")

        if analysis_attempted:
            log_lines.extend([
                "",
                "Analysis response:",
                "-" * 60,
                analysis_response if analysis_response else "(No response from analysis)",
                "-" * 60,
            ])

        if cli_command_args:
            log_lines.extend([
                "",
                "CLI command args:",
                "-" * 60,
                " ".join(cli_command_args) if isinstance(cli_command_args, list) else str(cli_command_args),
                "-" * 60,
            ])

        error_log_file.write_text("\n".join(log_lines), encoding="utf-8")

    def _get_status_analysis_prompt(self) -> Optional[str]:
        """取得分析 status code 的 prompt（子類覆寫）。

        當 agent response 中沒有 status code 時，會使用此 prompt 再呼叫一次 agent。
        子類應該覆寫此方法，提供 phase-specific 的分析 prompt。

        Returns:
            分析 status code 的 prompt，如果不支援分析則返回 None
        """
        return None

    def _read_issue_config(self, config_path: Path) -> Optional[Dict[str, Any]]:
        """Read issue configuration from config.yaml（共用方法）。

        Args:
            config_path: Path to config.yaml file

        Returns:
            Config data as dict if file exists, None otherwise
        """
        if not config_path.exists():
            return None

        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = yaml.safe_load(f)
            return config_data if config_data else None
        except (yaml.YAMLError, IOError):
            return None

    def _write_issue_config(self, config_path: Path, config_data: Dict[str, Any]) -> None:
        """Write issue configuration to config.yaml（共用方法）。

        Args:
            config_path: Path to config.yaml file
            config_data: Config data to write
        """
        config_path.parent.mkdir(parents=True, exist_ok=True)

        with open(config_path, 'w', encoding='utf-8') as f:
            yaml.dump(config_data, f, allow_unicode=True, default_flow_style=False)

    def _get_issue_config_value(self, config_file: Path, key: str) -> Optional[Any]:
        """Read a value from issue config file（共用方法）。

        Args:
            config_file: Path to config.yaml file
            key: Config key to read (e.g., "issue_id", "base_branch", "pr.auto_create")
                 Supports dot notation for nested keys

        Returns:
            Config value if found, None otherwise
        """
        config_data = self._read_issue_config(config_file)
        if not config_data:
            return None

        # Support dot notation for nested keys (e.g., "pr.auto_create")
        if '.' in key:
            keys = key.split('.')
            value = config_data
            for k in keys:
                if isinstance(value, dict):
                    value = value.get(k)
                    if value is None:
                        return None
                else:
                    return None
            return value
        else:
            return config_data.get(key)

    def _get_issue_dir(self, git_ops: "GitOperations") -> Path:
        """Get issue directory path based on current Git branch（共用方法）。

        Args:
            git_ops: GitOperations instance to get current branch

        Returns:
            Path to issue directory (.cafe/issues/{branch_name}/)
        """
        from pathlib import Path
        current_branch = git_ops.get_current_branch()
        return Path(f".cafe/issues/{current_branch}")

    def _get_current_branch_commits(self, git_ops: "GitOperations", base_branch: str) -> str:
        """Get commits from current branch that are not in base branch（共用方法）。

        這個方法用於獲取當前 feature branch 上新增的 commits，不包含 base branch 上的 commits。
        使用 git log base_branch..HEAD 格式來只顯示當前 branch 的 commits。

        Args:
            git_ops: GitOperations instance to get commits
            base_branch: Base branch name (e.g., "main", "develop")

        Returns:
            String containing commit list in oneline format
        """
        return git_ops.get_commits_between(base=base_branch, head="HEAD")

    def _get_versioned_file_path(
        self,
        phase_name: str,
        iteration: int,
        phase_dir: Path,
    ) -> Path:
        """取得版本化檔案的路徑（共用方法）。

        Args:
            phase_name: Phase 名稱（如 "spec", "plan", "review"）
            iteration: Iteration 編號（1-based）
            phase_dir: Phase 目錄路徑

        Returns:
            版本化檔案的 Path 物件（格式：{phase_name}_{iteration:03d}.md）

        Examples:
            >>> _get_versioned_file_path("spec", 1, Path(".cafe/issues/myissue/spec"))
            Path('.cafe/issues/myissue/spec/spec_001.md')
        """
        file_name = f"{phase_name}_{iteration:03d}.md"
        return phase_dir / file_name

    def _get_next_iteration_number(
        self,
        phase_name: str,
        phase_dir: Path,
    ) -> int:
        """取得下一個 iteration 編號（共用方法）。

        Args:
            phase_name: Phase 名稱（如 "spec", "plan", "review"）
            phase_dir: Phase 目錄路徑

        Returns:
            下一個 iteration 編號（從 1 開始）

        Raises:
            ValueError: 如果已有 999 個檔案
        """
        # Check if directory exists
        if not phase_dir.exists():
            return 1

        # Count existing {phase_name}_*.md files
        existing_files = list(phase_dir.glob(f"{phase_name}_*.md"))
        count = len(existing_files)

        # Check if exceeds 999
        if count >= 999:
            raise ValueError("不可超過 999")

        return count + 1

    def _copy_previous_version(
        self,
        phase_name: str,
        iteration: int,
        phase_dir: Path,
    ) -> None:
        """複製前一版本的檔案到新版本（共用方法）。

        Args:
            phase_name: Phase 名稱（如 "spec", "plan", "review"）
            iteration: 當前 iteration 編號
            phase_dir: Phase 目錄路徑

        Note:
            - 如果是第一輪（iteration=1），不執行任何動作
            - 如果前一版本不存在，不執行任何動作
        """
        # Do nothing for first iteration
        if iteration == 1:
            return

        # Get previous and current file paths
        prev_file = self._get_versioned_file_path(phase_name, iteration - 1, phase_dir)
        current_file = self._get_versioned_file_path(phase_name, iteration, phase_dir)

        # Copy if previous file exists
        if prev_file.exists():
            import shutil
            shutil.copy2(prev_file, current_file)

    def _get_latest_versioned_file(
        self,
        phase_name: str,
        phase_dir: Path,
    ) -> Optional[Path]:
        """取得最新版本的檔案路徑（共用方法）。

        Args:
            phase_name: Phase 名稱（如 "spec", "plan", "review"）
            phase_dir: Phase 目錄路徑

        Returns:
            最新版本檔案的 Path 物件，如果沒有任何版本則返回 None

        Examples:
            >>> _get_latest_versioned_file("spec", Path(".cafe/issues/myissue/spec"))
            Path('.cafe/issues/myissue/spec/spec_003.md')  # 假設有 3 個版本
        """
        # Find all versioned files
        pattern = f"{phase_name}_*.md"
        versioned_files = sorted(phase_dir.glob(pattern))

        # Return the latest one (highest number)
        if versioned_files:
            return versioned_files[-1]
        return None
