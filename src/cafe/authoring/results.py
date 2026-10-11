"""Deterministic public result and private prepared publication evidence."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Result:
    operation: str
    status: str = "ready"
    version: int = 1
    changes: list[dict] = field(default_factory=list)
    diff: str = ""
    diagnostics: list[dict] = field(default_factory=list)
    proposals: list[dict] = field(default_factory=list)
    artifact_summary: dict = field(default_factory=dict)
    transition_summary: dict = field(default_factory=dict)
    confirmation_gates: dict = field(default_factory=dict)
    simulation: dict = field(default_factory=dict)
    change_digest: str = ""
    files: dict[str, str] = field(default_factory=dict, repr=False)
    dependencies: dict[str, str | None] = field(default_factory=dict, repr=False)

    def diagnose(
        self,
        code,
        message,
        *,
        target=None,
        skill=None,
        step=None,
        field=None,
        severity="error",
        remedy="Supply an explicit bounded declaration and preview again",
    ):
        self.diagnostics.append(
            dict(
                code=code,
                severity=severity,
                target=target,
                skill=skill,
                step=step,
                field=field,
                message=message,
                remedy=remedy,
            )
        )
        if severity == "error":
            self.status = "rejected"

    def to_dict(self) -> dict[str, Any]:
        return {
            name: getattr(self, name)
            for name in (
                "version",
                "operation",
                "status",
                "changes",
                "diff",
                "diagnostics",
                "proposals",
                "artifact_summary",
                "transition_summary",
                "confirmation_gates",
                "simulation",
                "change_digest",
            )
        }
