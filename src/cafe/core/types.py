"""Core type definitions for CAFE."""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class AgentCLI(str, Enum):
    """Supported agent CLIs."""

    CLAUDE = "claude"
    GEMINI = "gemini"
    CURSOR = "cursor-agent"
    COPILOT = "copilot"


class PhaseStatus(str, Enum):
    """Phase execution status."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class CriticalPhaseError(Exception):
    """Critical error that should stop the entire workflow.
    
    This is used for errors like API rate limits or missing CLI tools,
    where continuing to the next phase would be pointless.
    """
    
    def __init__(self, message: str, error_type: str, phase_name: str):
        super().__init__(message)
        self.error_type = error_type
        self.phase_name = phase_name


class SpecRigor(str, Enum):
    """Specification rigor level."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class PermissionAction(str, Enum):
    """User action on permission request."""

    AUTHORIZE = "y"
    DIALOG = "t"
    SKIP = "s"


class AgentConfig(BaseModel):
    """Configuration for an AI agent.

    backup_clis 和 models_config 用於備份 agent 自動切換功能。
    當主要 CLI 遇到 rate limit 時，AgentManager 會依序嘗試 backup_clis 中的備份 CLI。

    config.yaml 設定範例::

        agents:
          developer:
            name: David
            cli: claude                    # 主要 CLI
            backup:                        # 備份 CLI（依序嘗試）
              - gemini
              - copilot
            models:                        # 各 CLI 對應的 phase model 設定
              claude:
                plan: opus
                develop: sonnet
              gemini:
                plan: gemini-2.5-pro-preview
                develop: gemini-2-flash-preview
              copilot: {}                  # 使用 CLI 預設 model
    """

    name: str
    cli: AgentCLI
    session_id: Optional[str] = None
    model: Optional[str] = None  # Optional model name for CLI (e.g., "sonnet", "opus")
    backup_clis: List["AgentCLI"] = Field(default_factory=list)  # 有序備份 CLI 列表
    models_config: Dict[str, Dict[str, str]] = Field(default_factory=dict)  # 各 CLI 對應的階段 model 設定


class TokenUsage(BaseModel):
    """Token usage and execution statistics."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    total_cost_usd: float = 0.0
    duration_ms: Optional[int] = None
    duration_api_ms: Optional[int] = None


class AgentResponse(BaseModel):
    """Response from agent execution."""

    response: str
    token_usage: TokenUsage
    permission_denials: List["PermissionDenial"] = Field(default_factory=list)
    cli_command_args: Optional[List[str]] = None  # CLI command arguments (excluding prompt)
    streaming_log: List[str] = Field(default_factory=list)  # Streaming fragment history
    model: Optional[str] = None  # Model name


class PhaseResult(BaseModel):
    """Result of a phase execution."""

    status: PhaseStatus
    message: str = ""
    data: Dict[str, Any] = Field(default_factory=dict)
    token_usage: Optional[TokenUsage] = None


class PermissionRequest(BaseModel):
    """Permission request from agent."""

    tool_name: str
    tool_input: Dict[str, Any]


class PermissionDenial(BaseModel):
    """Permission denial from agent CLI."""

    tool_name: str
    tool_input: Dict[str, Any]

    def to_allowed_tool_pattern(self) -> str:
        """Convert permission denial to allowed_tools pattern.

        Returns:
            Pattern like "read", "write(/file_path)", or "bash(git status)"
            Note:
            - Tool names are lowercase to match CLI conventions
            - File paths use git ignore rules: /path means project root
        """
        import os
        from pathlib import Path

        # Extract common parameters
        file_path = self.tool_input.get("file_path")
        command = self.tool_input.get("command")

        # Convert tool name to lowercase to match CLI conventions
        tool_name_lower = self.tool_name.lower()

        if file_path:
            # Convert absolute path to relative path (git ignore rules)
            path = Path(file_path)
            if path.is_absolute():
                # Get project root (current working directory)
                project_root = Path(os.getcwd())
                try:
                    # Convert to relative path
                    relative_path = path.relative_to(project_root)
                    # Add / prefix (git ignore: /path means project root)
                    file_path = f"/{relative_path}"
                except ValueError:
                    # If path is outside project root, keep absolute path
                    pass
            else:
                # Relative path - add / prefix
                file_path = f"/{file_path}"

            return f"{tool_name_lower}({file_path})"
        elif command:
            # For bash commands, use first two words as pattern
            cmd_parts = command.split()
            if len(cmd_parts) >= 2:
                return f"{tool_name_lower}({cmd_parts[0]} {cmd_parts[1]})"
            else:
                return f"{tool_name_lower}({cmd_parts[0]})"
        else:
            # If no specific params, just allow the tool
            return tool_name_lower


class SessionData(BaseModel):
    """Session data for an agent."""

    agent_name: str
    cli: AgentCLI
    session_id: str
    created_at: datetime
    last_used_at: datetime


class SessionConfig(BaseModel):
    """Session configuration."""

    issue_id: Optional[str] = None
    spec_file: Optional[str] = None
    sessions_dir: str = ".cafe/sessions"
    issue_dir: str = ".cafe/issues"


class PhaseProgress(BaseModel):
    """Phase execution progress and status."""

    phase: str
    status: PhaseStatus
    status_code: Optional[str] = None
    timestamp: datetime
    iteration: Optional[int] = None
    message: str = ""
    end_time: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        result = {
            "phase": self.phase,
            "status": self.status.value,
            "status_code": self.status_code,
            "timestamp": self.timestamp.isoformat(),
            "iteration": self.iteration,
            "message": self.message,
        }
        if self.end_time:
            result["end_time"] = self.end_time.isoformat()
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PhaseProgress":
        """Create from dictionary."""
        # Handle 'Z' suffix in timestamp (convert to +00:00 for fromisoformat)
        timestamp_str = data["timestamp"]
        if timestamp_str.endswith('Z'):
            timestamp_str = timestamp_str.replace('Z', '+00:00')

        end_time = None
        end_time_str = data.get("end_time")
        if end_time_str:
            if end_time_str.endswith('Z'):
                end_time_str = end_time_str.replace('Z', '+00:00')
            end_time = datetime.fromisoformat(end_time_str)

        return cls(
            phase=data["phase"],
            status=PhaseStatus(data["status"]),
            status_code=data.get("status_code"),
            timestamp=datetime.fromisoformat(timestamp_str),
            iteration=data.get("iteration"),
            message=data.get("message", ""),
            end_time=end_time,
        )
