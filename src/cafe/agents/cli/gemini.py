"""Gemini CLI 工具實作."""

import json
from pathlib import Path
from typing import List, Optional, Tuple

from cafe.agents.cli.abstract import AbstractCLI
from cafe.core.types import PermissionDenial, TokenUsage


class GeminiCLI(AbstractCLI):
    """Gemini CLI 工具的具體實作."""

    def build_command(
        self,
        prompt: str,
        allowed_tools: Optional[List[str]] = None,
        allowed_directories: Optional[List[str]] = None,
    ) -> List[str]:
        """建構 Gemini CLI 命令列參數.

        參數順序: gemini -> -p -> --resume -> --model -> --allowed-tools -> --output-format -> --include-directories

        Args:
            prompt: 提示詞
            allowed_tools: 允許使用的工具列表 (已轉換格式)
            allowed_directories: 允許存取的目錄列表

        Returns:
            完整的命令列參數列表
        """
        # 確保 .geminiignore 檔案存在
        self.ensure_geminiignore()

        cmd = ["gemini", "-p", prompt]

        # 如果有 session_id，加入 --resume 參數
        if self.config.session_id:
            cmd.extend(["--resume", self.config.session_id])

        # 如果有 model，加入 --model 參數
        if self.config.model:
            cmd.extend(["--model", self.config.model])

        # 如果有 allowed_tools，加入 --allowed-tools 參數
        if allowed_tools:
            tools_arg_value = ",".join(allowed_tools)
            cmd.extend(["--allowed-tools", tools_arg_value])

        # 加入輸出格式參數
        cmd.extend(self.get_output_format())

        # 如果有 allowed_directories，加入 --include-directories 參數
        if allowed_directories:
            cmd = self.add_directories(cmd, allowed_directories)

        return cmd

    def parse_response(
        self,
        output_lines: List[str],
        streaming_log: Optional[List[str]] = None,
    ) -> Tuple[str, TokenUsage, List[PermissionDenial]]:
        """解析 Gemini CLI 的 stream-json 輸出.

        Args:
            output_lines: CLI 輸出的行列表
            streaming_log: 串流輸出記錄 (可選，此處不使用)

        Returns:
            (response, token_usage, permission_denials) 三元組
        """
        # 提取 assistant 訊息，過濾 user prompt echo
        assistant_messages = []
        permission_denials = []
        token_usage = TokenUsage()
        full_response = ""

        for line in output_lines:
            try:
                data = json.loads(line.strip())

                # 提取 assistant 訊息 (過濾 user 訊息)
                if data.get("type") == "message" and data.get("role") == "assistant":
                    content = data.get("content", "")
                    if content:
                        assistant_messages.append(content)

                # 提取最後一行的 response 作為 fallback
                if "response" in data:
                    full_response = data["response"]

                # 提取 permission denials
                # 註：假設 Gemini 使用類似 Claude 的格式，若實際格式不同需調整
                if "permission_denials" in data and data["permission_denials"]:
                    for denial_data in data["permission_denials"]:
                        permission_denials.append(
                            PermissionDenial(
                                tool_name=denial_data.get("tool_name", ""),
                                tool_input=denial_data.get("tool_input", {})
                            )
                        )

            except json.JSONDecodeError:
                # 非 JSON 行，忽略
                continue

        # 如果有提取到 assistant 訊息，使用它們；否則使用 fallback response
        response = "".join(assistant_messages) if assistant_messages else full_response

        return response, token_usage, permission_denials

    def translate_allowed_tools(self, tools: List[str]) -> List[str]:
        """轉換工具名稱為 Gemini 格式.

        特殊處理: write_file 不支援路徑參數，需要去除路徑部分。

        Args:
            tools: 工具名稱列表

        Returns:
            轉換後的工具名稱列表
        """
        processed_tools = []

        for tool in tools:
            # 特殊處理: write_file 不支援路徑參數
            if tool.startswith("write_file("):
                tool_name = "write_file"
            else:
                tool_name = tool

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
            cmd.extend(["--include-directories", directory])
        return cmd

    def get_output_format(self) -> List[str]:
        """取得 Gemini CLI 的輸出格式參數.

        Returns:
            輸出格式相關的命令列參數
        """
        return ["--output-format", "stream-json"]

    def extract_session_id(self, output_lines: List[str]) -> Optional[str]:
        """從輸出中提取 session ID.

        Gemini 在 init 訊息中提供 session_id。

        Args:
            output_lines: CLI 輸出的行列表

        Returns:
            Session ID，如果找不到則回傳 None
        """
        for line in output_lines:
            try:
                data = json.loads(line.strip())
                # Gemini 在 init 訊息中提供 session_id
                if data.get("type") == "init" and "session_id" in data:
                    return data["session_id"]
            except json.JSONDecodeError:
                continue
        return None

    def ensure_geminiignore(self) -> None:
        """確保 .geminiignore 檔案存在並包含必要的配置.

        Gemini CLI 需要 .geminiignore 來排除 .cafe 目錄。
        """
        geminiignore_path = Path(".geminiignore")
        required_pattern = "!/.cafe"

        # 檢查檔案是否存在且包含必要的 pattern
        if geminiignore_path.exists():
            content = geminiignore_path.read_text()
            if required_pattern in content:
                return  # 已經正確配置

            # 檔案存在但缺少 pattern - 附加它
            with open(geminiignore_path, "a") as f:
                if not content.endswith("\n"):
                    f.write("\n")
                f.write(f"{required_pattern}\n")
        else:
            # 建立新的 .geminiignore 檔案
            geminiignore_path.write_text(f"{required_pattern}\n")
