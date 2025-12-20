"""Claude CLI 工具實作."""

import json
from pathlib import Path
from typing import List, Optional, Tuple

from cafe.agents.cli.abstract import AbstractCLI
from cafe.core.types import PermissionDenial, TokenUsage
from cafe.utils.git_utils import get_repo_root, to_git_ignore_path


class ClaudeCLI(AbstractCLI):
    """Claude CLI 工具的具體實作."""

    def build_command(
        self,
        prompt: str,
        allowed_tools: Optional[List[str]] = None,
        allowed_directories: Optional[List[str]] = None,
    ) -> List[str]:
        """建構 Claude CLI 命令列參數.

        參數順序: claude -> --resume -> -p -> --model -> --allowed-tools -> --output-format -> --add-dir

        Args:
            prompt: 提示詞
            allowed_tools: 允許使用的工具列表 (已轉換格式)
            allowed_directories: 允許存取的目錄列表

        Returns:
            完整的命令列參數列表
        """
        cmd = ["claude"]

        # 1. 如果有 session_id，加入 --resume 參數 (必須在 -p 之前)
        if self.config.session_id:
            cmd.extend(["--resume", self.config.session_id])

        # 2. 加入 -p 參數 (永遠在最前面，除了 --resume)
        cmd.extend(["-p", prompt])

        # 3. 如果有 model，加入 --model 參數 (必須在 -p 之後)
        if self.config.model:
            cmd.extend(["--model", self.config.model])

        # 4. 如果有 allowed_tools，加入 --allowed-tools 參數
        if allowed_tools:
            tools_arg_value = ",".join(allowed_tools)
            cmd.extend(["--allowed-tools", tools_arg_value])

        # 5. 加入輸出格式參數
        cmd.extend(self.get_output_format())

        # 6. 如果有 allowed_directories，加入 --add-dir 參數
        if allowed_directories:
            cmd = self.add_directories(cmd, allowed_directories)

        return cmd

    def parse_response(
        self,
        output_lines: List[str],
        streaming_log: Optional[List[str]] = None,
    ) -> Tuple[str, TokenUsage, List[PermissionDenial]]:
        """解析 Claude CLI 的 stream-json 輸出.

        Args:
            output_lines: CLI 輸出的行列表
            streaming_log: 串流輸出記錄 (可選，此處不使用)

        Returns:
            (response, token_usage, permission_denials) 三元組
        """
        response_text = ""
        token_usage = TokenUsage()
        permission_denials = []

        for line in output_lines:
            try:
                data = json.loads(line.strip())

                # 提取內容 (新格式: message.content[] 或舊格式: content)
                if "message" in data and "content" in data["message"]:
                    for content_block in data["message"]["content"]:
                        if content_block.get("type") == "text":
                            response_text = content_block.get("text", "")
                elif "content" in data:
                    response_text = data["content"]

                # 提取 token usage
                if "usage" in data:
                    usage_data = data["usage"]
                    token_usage = TokenUsage(
                        input_tokens=usage_data.get("input_tokens", 0),
                        output_tokens=usage_data.get("output_tokens", 0),
                        cache_creation_input_tokens=usage_data.get("cache_creation_input_tokens", 0),
                        cache_read_input_tokens=usage_data.get("cache_read_input_tokens", 0),
                    )

                if "total_cost_usd" in data:
                    token_usage.total_cost_usd = data["total_cost_usd"]

                # 提取 permission denials
                if "permission_denials" in data and data["permission_denials"]:
                    for denial_data in data["permission_denials"]:
                        permission_denials.append(
                            PermissionDenial(
                                tool_name=denial_data["tool_name"],
                                tool_input=denial_data["tool_input"]
                            )
                        )

            except json.JSONDecodeError:
                # 非 JSON 行，忽略
                continue

        return response_text, token_usage, permission_denials

    def translate_allowed_tools(self, tools: List[str]) -> List[str]:
        """轉換工具名稱為 Claude 格式 (首字母大寫，路徑轉為 git-ignore 格式).

        Args:
            tools: 工具名稱列表 (小寫格式，例如 ["read", "write(/path)"])

        Returns:
            轉換後的工具名稱列表 (例如 ["Read", "Write(/.cafe/config.yaml)"])
        """
        processed_tools = []

        for tool in tools:
            # 處理帶有路徑或命令的工具 (例如 write(/path) 或 bash(git status))
            if "(" in tool and ")" in tool:
                tool_name = tool.split("(")[0].lower()
                path_or_cmd = tool.split("(")[1].rstrip(")")

                # 判斷是否為路徑或命令
                # 如果 tool_name 是 bash，則視為命令，不轉換路徑格式
                if tool_name == "bash":
                    # 命令參數，直接使用，不加 / 前綴
                    processed_tool = f"{tool_name.capitalize()}({path_or_cmd})"
                else:
                    # 路徑參數，需要轉換為 git-ignore 格式
                    path_obj = Path(path_or_cmd)

                    # 區分路徑類型:
                    # 1. 已經是 git ignore 格式 (以 / 開頭但不是絕對路徑)
                    # 2. 絕對路徑 (系統路徑，例如 /Users/...)
                    # 3. 相對路徑 (例如 .cafe/... 或 src/...)
                    is_git_ignore_format = path_or_cmd.startswith("/") and not path_obj.is_absolute()

                    if is_git_ignore_format:
                        # 已經是 git ignore 格式，保持不變
                        processed_tool = f"{tool_name.capitalize()}({path_or_cmd})"
                    elif path_obj.is_absolute():
                        # 絕對路徑，轉換為 git ignore 格式
                        try:
                            repo_root = get_repo_root()
                            git_ignore_path = to_git_ignore_path(path_obj, repo_root)
                            processed_tool = f"{tool_name.capitalize()}({git_ignore_path})"
                        except (ValueError, OSError):
                            # 轉換失敗，使用原始路徑
                            processed_tool = f"{tool_name.capitalize()}({path_or_cmd})"
                    else:
                        # 相對路徑，加上 / 前綴 (git ignore 格式)
                        git_ignore_path = "/" + path_or_cmd
                        processed_tool = f"{tool_name.capitalize()}({git_ignore_path})"
            else:
                # 工具沒有路徑參數，直接首字母大寫
                processed_tool = tool.capitalize()

            # 去重
            if processed_tool not in processed_tools:
                processed_tools.append(processed_tool)

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
        """取得 Claude CLI 的輸出格式參數.

        Returns:
            輸出格式相關的命令列參數
        """
        return ["--output-format", "stream-json", "--verbose"]

    def extract_session_id(self, output_lines: List[str]) -> Optional[str]:
        """從輸出中提取 session ID.

        Args:
            output_lines: CLI 輸出的行列表

        Returns:
            Session ID，如果找不到則回傳 None
        """
        for line in output_lines:
            try:
                data = json.loads(line.strip())
                if "session_id" in data:
                    return data["session_id"]
            except json.JSONDecodeError:
                continue
        return None
