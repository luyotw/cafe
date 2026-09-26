"""Driver-owned, authority-bound completion of a neutral HumanTask."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Mapping

from ._store import contract_lock
from .task_inspection import inspect_task_authority


def complete_driver_task(
    issue_dir: Path,
    task_id: str,
    *,
    response: Mapping[str, Any],
    evidence: Mapping[str, Any],
    contract_sha256: str,
    sources_sha256: str,
) -> dict[str, Any]:
    """Recheck the exact decision under the replacement lock before durable use."""
    issue_dir = Path(issue_dir).resolve()
    with contract_lock(issue_dir):
        facts = inspect_task_authority(issue_dir, task_id, response=response, evidence=evidence)
        if (
            not facts["allowed"]
            or facts["contract_sha256"] != contract_sha256
            or facts["sources_sha256"] != sources_sha256
        ):
            raise ValueError("Driver task authority changed or is insufficient")
        result = subprocess.run(
            [
                "cafe",
                "task",
                "complete",
                task_id,
                "--result",
                json.dumps(response, ensure_ascii=False),
                "--no-resume",
                "--json",
            ],
            cwd=issue_dir.parent.parent.parent,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise ValueError("durable task completion failed: " + result.stderr.strip()[:500])
        completed = json.loads(result.stdout)
        if not isinstance(completed, dict):
            raise ValueError("task completion returned an invalid result")
        return completed
