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
        """Save iteration history to JSON file (共用方法).

        將 iteration history 儲存為 JSON 檔案。所有 Phase 都可以使用此方法。

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
