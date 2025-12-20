"""Copilot CLI 工具實作."""

import time
from pathlib import Path
from typing import List, Optional, Set, Tuple

from cafe.agents.cli.abstract import AbstractCLI
from cafe.core.types import PermissionDenial, TokenUsage


class CopilotCLI(AbstractCLI):
    """Copilot CLI 工具的具體實作."""

    def __init__(self, config):
        """初始化 Copilot CLI.

        Args:
            config: Agent 配置
        """
        super().__init__(config)
        self._existing_sessions: Set[str] = set()

    def build_command(
        self,
        prompt: str,
        allowed_tools: Optional[List[str]] = None,
        allowed_directories: Optional[List[str]] = None,
    ) -> List[str]:
        """建構 Copilot CLI 命令列參數.

        參數順序: copilot -> -p -> --model -> --allow-tool/--allow-all-tools -> --resume -> --add-dir

        Args:
            prompt: 提示詞
            allowed_tools: 允許使用的工具列表 (已轉換格式)
            allowed_directories: 允許存取的目錄列表

        Returns:
            完整的命令列參數列表
        """
        cmd = ["copilot", "-p", prompt]

        # 如果有 model，加入 --model 參數
        if self.config.model:
            cmd.extend(["--model", self.config.model])

        # 加入 allowed tools 參數
        if allowed_tools:
            # 使用 --allow-tool 為每個工具
            for tool in allowed_tools:
                cmd.extend(["--allow-tool", tool])
        else:
            # 使用 --allow-all-tools 自動批准所有工具
            cmd.append("--allow-all-tools")

        # 如果有 session_id，加入 --resume 參數
        if self.config.session_id:
            cmd.extend(["--resume", self.config.session_id])

        # 如果有 allowed_directories，加入 --add-dir 參數
        if allowed_directories:
            cmd = self.add_directories(cmd, allowed_directories)

        return cmd

    def parse_response(
        self,
        output_lines: List[str],
        streaming_log: Optional[List[str]] = None,
    ) -> Tuple[str, TokenUsage, List[PermissionDenial]]:
        """解析 Copilot CLI 的純文字輸出.

        Args:
            output_lines: CLI 輸出的行列表
            streaming_log: 串流輸出記錄 (可選，此處不使用)

        Returns:
            (response, token_usage, permission_denials) 三元組
        """
        # Copilot 使用純文字輸出，連接所有行
        response = "".join(output_lines)
        token_usage = TokenUsage()
        permission_denials = []

        # TODO: 如果 Copilot 提供 token usage 或 permission denials，在此解析

        return response, token_usage, permission_denials

    def translate_allowed_tools(self, tools: List[str]) -> List[str]:
        """轉換工具名稱為 Copilot 格式.

        Copilot 使用簡單的工具名稱，需要去除路徑參數。

        Args:
            tools: 工具名稱列表

        Returns:
            轉換後的工具名稱列表
        """
        processed_tools = []

        for tool in tools:
            # 如果工具名稱帶有路徑參數，去除路徑部分
            if "(" in tool and ")" in tool:
                tool_name = tool.split("(")[0].lower()
            else:
                tool_name = tool.lower()

            # 去重
            if tool_name not in processed_tools:
                processed_tools.append(tool_name)

        return processed_tools

    def add_directories(self, cmd: List[str], directories: List[str]) -> List[str]:
        """將允許的目錄加入命令列參數.

        Args:
            cmd: 目前的命令列參數
            directories: 目錄列表

        Returns:
            更新後的命令列參數
        """
        for directory in directories:
            cmd.extend(["--add-dir", directory])
        return cmd

    def get_output_format(self) -> List[str]:
        """取得 Copilot CLI 的輸出格式參數.

        Returns:
            空列表（Copilot 不使用 output format 參數）
        """
        # Copilot 不使用 output format 參數
        return []

    def record_existing_sessions(self) -> None:
        """記錄執行前已存在的 session 檔案.

        用於稍後檢測新建立的 session。
        """
        copilot_session_dir = Path.home() / ".copilot" / "session-state"

        if copilot_session_dir.exists():
            self._existing_sessions = {
                f.name for f in copilot_session_dir.iterdir() if f.is_file()
            }
        else:
            self._existing_sessions = set()

    def extract_session_id(self, output_lines: List[str]) -> Optional[str]:
        """從檔案系統提取新建立的 session ID.

        Copilot 自動建立 session 檔案，需要從檔案系統檢測。

        Args:
            output_lines: CLI 輸出的行列表 (此處不使用)

        Returns:
            Session ID，如果找不到則回傳 None
        """
        copilot_session_dir = Path.home() / ".copilot" / "session-state"

        if not copilot_session_dir.exists():
            return None

        # 等待檔案系統更新
        time.sleep(0.1)

        # 取得目前的 session 檔案
        current_sessions = {
            f.name for f in copilot_session_dir.iterdir() if f.is_file()
        }

        # 找出新建立的 session
        new_sessions = current_sessions - self._existing_sessions

        if new_sessions:
            # 取得最新的 session (依名稱排序)
            newest_session = sorted(new_sessions)[-1]
            # 去除 .jsonl 副檔名
            session_id = newest_session.replace(".jsonl", "")
            return session_id

        return None
