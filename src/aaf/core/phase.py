"""Base class for workflow phases."""

import json
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from aaf.core.status_codes import PhaseStatusCode
from aaf.core.types import PhaseResult


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
