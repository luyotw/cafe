"""Base class for workflow phases."""

import json
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from aaf.core.status_codes import PhaseStatusCode
from aaf.core.types import PhaseResult, PhaseStatus


class Phase(ABC):
    """Abstract base class for all workflow phases.

    Each phase represents a step in the AAF workflow (e.g., requirements clarification,
    implementation analysis, development, code review, etc.).

    Subclasses must implement the execute() method to define the phase's behavior.
    """

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

        # 更新共用的 agent metadata
        history_data["prompt"] = prompt
        history_data["cli"] = agent_cli
        history_data["session_id"] = agent_session_id
        history_data["allowed_tools"] = allowed_tools
        history_data["denied_tools"] = denied_tools
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

        # 4. 執行 agent
        response, token_usage = self.agent_manager.execute(
            agent_name,
            prompt,
            allowed_tools=allowed_tools,
            denied_tools=denied_tools,
        )

        # 5. 檢查空回應
        no_response_status = self._check_empty_response(response)
        if no_response_status:
            # Agent 返回空回應 - 保存並返回 NO_RESPONSE
            self._update_iteration_history(
                phase_specific_data={"response": response},
                prompt=prompt,
                agent_cli=agent_cli,
                agent_session_id=agent_session_id,
                allowed_tools=allowed_tools,
                denied_tools=denied_tools,
                status_code=no_response_status,
            )
            return response, no_response_status

        # 6. 提取 status code
        from aaf.core.status_codes import StatusCodeParser
        status_code = StatusCodeParser.extract(
            response,
            valid_codes=valid_status_codes,
        )

        # 7. 更新 history（總是保存，即使沒有 status code）
        self._update_iteration_history(
            phase_specific_data={"response": response},
            prompt=prompt,
            agent_cli=agent_cli,
            agent_session_id=agent_session_id,
            allowed_tools=allowed_tools,
            denied_tools=denied_tools,
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
            from aaf.ui.display import Display
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
            from aaf.ui.display import Display
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
            return PhaseResult(
                status=PhaseStatus.FAILED,
                message=f"Phase rejected in iteration {self.iteration}",
                data={
                    "iterations": self.iteration,
                    "final_response": response,
                    "status_code": status_code.value,
                },
            )

        # Handle complete codes (e.g., READY_FOR_REVIEW, CONFIRMED)
        # These typically trigger user confirmation in next iteration
        if status_code in complete_codes:
            return None  # Continue to next iteration

        # Handle continue codes (e.g., NEED_CLARIFICATION)
        if status_code in continue_codes:
            return None  # Continue to next iteration

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
