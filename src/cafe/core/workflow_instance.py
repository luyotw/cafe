"""Workflow instance model for playbook-based execution."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


WORKFLOW_INSTANCE_FILENAME = "workflow_instance.json"


@dataclass
class WorkflowInstance:
    """Represents a workflow execution under one issue directory."""

    issue_name: str
    root_dir: Path
    playbook_id: str
    current_step: str
    status: str = "in_progress"
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def file_path(self) -> Path:
        return self.root_dir / WORKFLOW_INSTANCE_FILENAME

    def to_dict(self) -> Dict[str, Any]:
        return {
            "issue_name": self.issue_name,
            "playbook_id": self.playbook_id,
            "current_step": self.current_step,
            "status": self.status,
            "metadata": self.metadata,
            "updated_at": datetime.now().astimezone().isoformat(),
        }

    def save(self) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, issue_dir: Path) -> Optional["WorkflowInstance"]:
        file_path = issue_dir / WORKFLOW_INSTANCE_FILENAME
        if not file_path.exists():
            return None

        raw = json.loads(file_path.read_text(encoding="utf-8"))
        return cls(
            issue_name=raw["issue_name"],
            root_dir=issue_dir,
            playbook_id=raw["playbook_id"],
            current_step=raw["current_step"],
            status=raw.get("status", "in_progress"),
            metadata=raw.get("metadata", {}),
        )

    @classmethod
    def load_or_create(
        cls,
        issue_dir: Path,
        playbook_id: str,
        initial_step: str,
    ) -> "WorkflowInstance":
        instance = cls.load(issue_dir)
        if instance is not None:
            return instance

        instance = cls(
            issue_name=issue_dir.name,
            root_dir=issue_dir,
            playbook_id=playbook_id,
            current_step=initial_step,
        )
        instance.save()
        return instance

    def transition_to(self, next_step: str, status_code: str) -> None:
        self.current_step = next_step
        self.status = "in_progress"
        self.metadata["last_status_code"] = status_code
        self.save()

    def mark_completed(self, status_code: str) -> None:
        self.status = "completed"
        self.metadata["last_status_code"] = status_code
        self.save()
