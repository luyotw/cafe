"""Core type definitions for AAF."""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class WorkflowMode(str, Enum):
    """Workflow execution mode."""

    GITHUB = "github"
    LOCAL = "local"


class AgentCLI(str, Enum):
    """Supported agent CLIs."""

    CLAUDE = "claude"
    GEMINI = "gemini"
    CURSOR = "cursor-agent"


class PhaseStatus(str, Enum):
    """Phase execution status."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class PermissionAction(str, Enum):
    """User action on permission request."""

    AUTHORIZE = "y"
    DIALOG = "t"
    SKIP = "s"


class AgentConfig(BaseModel):
    """Configuration for an AI agent."""

    name: str
    cli: AgentCLI
    session_id: Optional[str] = None


class TokenUsage(BaseModel):
    """Token usage statistics."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    total_cost_usd: float = 0.0


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


class SessionConfig(BaseModel):
    """Session configuration."""

    workflow_mode: WorkflowMode
    issue_id: Optional[str] = None
    spec_file: Optional[str] = None
    sessions_dir: str = ".aaf/sessions"
    issue_dir: str = ".aaf/issues"


class PhaseProgress(BaseModel):
    """Phase execution progress and status."""

    phase: str
    status: PhaseStatus
    status_code: Optional[str] = None
    timestamp: datetime
    iteration: Optional[int] = None
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "phase": self.phase,
            "status": self.status.value,
            "status_code": self.status_code,
            "timestamp": self.timestamp.isoformat(),
            "iteration": self.iteration,
            "message": self.message,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PhaseProgress":
        """Create from dictionary."""
        return cls(
            phase=data["phase"],
            status=PhaseStatus(data["status"]),
            status_code=data.get("status_code"),
            timestamp=datetime.fromisoformat(data["timestamp"]),
            iteration=data.get("iteration"),
            message=data.get("message", ""),
        )
