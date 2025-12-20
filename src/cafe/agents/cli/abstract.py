"""抽象基底類別，定義所有 CLI 工具的共同介面."""

from abc import ABC, abstractmethod
from typing import List, Optional, Tuple

from cafe.core.types import AgentConfig, PermissionDenial, TokenUsage


class AbstractCLI(ABC):
    """CLI 工具的抽象基底類別.

    所有 CLI 工具 (Claude, Gemini, Cursor, Copilot) 都必須繼承此類別
    並實作所有抽象方法。
    """

    def __init__(self, config: AgentConfig) -> None:
        """初始化 CLI 策略.

        Args:
            config: Agent 配置
        """
        self.config = config

    @abstractmethod
    def build_command(
        self,
        prompt: str,
        allowed_tools: Optional[List[str]] = None,
        allowed_directories: Optional[List[str]] = None,
    ) -> List[str]:
        """建構 CLI 命令列參數.

        Args:
            prompt: 提示詞
            allowed_tools: 允許使用的工具列表
            allowed_directories: 允許存取的目錄列表

        Returns:
            完整的命令列參數列表
        """
        pass

    @abstractmethod
    def parse_response(
        self,
        output_lines: List[str],
        streaming_log: Optional[List[str]] = None,
    ) -> Tuple[str, TokenUsage, List[PermissionDenial]]:
        """解析 CLI 輸出.

        Args:
            output_lines: CLI 輸出的行列表
            streaming_log: 串流輸出記錄 (可選)

        Returns:
            (response, token_usage, permission_denials) 三元組
        """
        pass

    @abstractmethod
    def translate_allowed_tools(self, tools: List[str]) -> List[str]:
        """轉換工具名稱為此 CLI 的格式.

        Args:
            tools: 工具名稱列表 (使用內部慣例格式)

        Returns:
            轉換後的工具名稱列表
        """
        pass

    @abstractmethod
    def add_directories(self, cmd: List[str], directories: List[str]) -> List[str]:
        """將允許的目錄加入命令列參數.

        Args:
            cmd: 目前的命令列參數
            directories: 目錄列表

        Returns:
            更新後的命令列參數
        """
        pass

    @abstractmethod
    def get_output_format(self) -> List[str]:
        """取得此 CLI 的輸出格式參數.

        Returns:
            輸出格式相關的命令列參數 (例如 ["--output-format", "stream-json"])
        """
        pass

    @abstractmethod
    def extract_session_id(self, output_lines: List[str]) -> Optional[str]:
        """從輸出中提取 session ID.

        Args:
            output_lines: CLI 輸出的行列表

        Returns:
            Session ID，如果找不到則回傳 None
        """
        pass
