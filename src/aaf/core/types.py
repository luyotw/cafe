"""Core type definitions for AAF."""

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class WorkflowMode(str, Enum):
    """Workflow execution mode."""

    GITHUB = "github"
    LOCAL = "local"


class AgentTool(str, Enum):
    """Supported agent tools."""

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
    tool: AgentTool
    session_id: Optional[str] = None
    allowed_tools: List[str] = Field(default_factory=list)


class PhaseResult(BaseModel):
    """Result of a phase execution."""

    status: PhaseStatus
    message: str = ""
    data: Dict[str, Any] = Field(default_factory=dict)


class PermissionRequest(BaseModel):
    """Permission request from agent."""

    tool_name: str
    tool_input: Dict[str, Any]


class SessionConfig(BaseModel):
    """Session configuration."""

    workflow_mode: WorkflowMode
    issue_id: Optional[str] = None
    requirements_file: Optional[str] = None
    sessions_dir: str = ".aaf/sessions"
    issue_dir: str = ".aaf/issues"
