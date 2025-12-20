"""Cursor CLI 工具實作."""

import json
from typing import List, Optional, Tuple

from cafe.agents.cli.abstract import AbstractCLI
from cafe.core.types import PermissionDenial, TokenUsage


class CursorCLI(AbstractCLI):
    """Cursor CLI 工具的具體實作."""

    def build_command(
        self,
        prompt: str,
        allowed_tools: Optional[List[str]] = None,
        allowed_directories: Optional[List[str]] = None,
    ) -> List[str]:
        """建構 Cursor CLI 命令列參數.

        注意: Cursor 不支援工具限制和目錄限制，一律使用 --force 自動批准所有工具。

        參數順序: cursor-agent -> -p -> --model -> --resume -> --force -> --output-format

        Args:
            prompt: 提示詞
            allowed_tools: 允許使用的工具列表 (Cursor 不支援，會被忽略)
            allowed_directories: 允許存取的目錄列表 (Cursor 不支援，會被忽略)

        Returns:
            完整的命令列參數列表
        """
        cmd = ["cursor-agent", "-p", prompt]

        # 如果有 model，加入 --model 參數
        if self.config.model:
            cmd.extend(["--model", self.config.model])

        # 如果有 session_id，加入 --resume 參數
        if self.config.session_id:
            cmd.extend(["--resume", self.config.session_id])

        # Cursor 不支援 allowed-tools，使用 --force 自動批准所有工具
        cmd.append("--force")

        # 加入輸出格式參數
        cmd.extend(self.get_output_format())

        return cmd

    def parse_response(
        self,
        output_lines: List[str],
        streaming_log: Optional[List[str]] = None,
    ) -> Tuple[str, TokenUsage, List[PermissionDenial]]:
        """解析 Cursor CLI 的 JSON 輸出.

        Args:
            output_lines: CLI 輸出的行列表
            streaming_log: 串流輸出記錄 (可選，此處不使用)

        Returns:
            (response, token_usage, permission_denials) 三元組
        """
        full_output = "".join(output_lines)
        token_usage = TokenUsage()
        permission_denials = []

        # 解析 JSON 輸出
        try:
            data = json.loads(full_output.strip())
            response = data.get("response", full_output)
            # TODO: 如果 Cursor 提供 token usage 或 permission denials，在此解析
        except json.JSONDecodeError:
            # 如果不是 JSON，回傳原始輸出
            response = full_output

        return response, token_usage, permission_denials

    def translate_allowed_tools(self, tools: List[str]) -> List[str]:
        """轉換工具名稱為 Cursor 格式.

        Cursor 不支援工具限制，回傳空列表。

        Args:
            tools: 工具名稱列表

        Returns:
            空列表（Cursor 不支援工具限制）
        """
        # Cursor 不支援工具限制，回傳空列表
        return []

    def add_directories(self, cmd: List[str], directories: List[str]) -> List[str]:
        """將允許的目錄加入命令列參數.

        Cursor 不支援目錄限制，直接回傳原始命令。

        Args:
            cmd: 目前的命令列參數
            directories: 目錄列表

        Returns:
            原始命令（不做任何改變）
        """
        # Cursor 不支援目錄限制，回傳原始命令
        return cmd

    def get_output_format(self) -> List[str]:
        """取得 Cursor CLI 的輸出格式參數.

        Returns:
            輸出格式相關的命令列參數
        """
        return ["--output-format", "json"]

    def extract_session_id(self, output_lines: List[str]) -> Optional[str]:
        """從輸出中提取 session ID.

        Cursor 自動管理 session，不需要從輸出提取。

        Args:
            output_lines: CLI 輸出的行列表

        Returns:
            None（Cursor 自動管理 session）
        """
        # Cursor 自動管理 session，不需要從輸出提取
        return None
