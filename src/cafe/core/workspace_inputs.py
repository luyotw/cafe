"""Workspace artifact guard shared by agent and automatic consumers."""

from typing import Any
import json
from pathlib import Path
from cafe.core.blackboard import ArtifactKind
from cafe.core.workspace_artifact import (
    WorkspaceArtifact,
    WorkspaceArtifactError,
    verify_workspace_artifact,
)


def validate_workspace_inputs(artifacts, *, root, step_def=None, verified_workspace=None):
    required_name = step_def.get("workspace_input_artifact") if step_def is not None else None
    if isinstance(required_name, str):
        required_entry = artifacts.get(required_name)
        if required_entry is None:
            raise ValueError(
                f"required workspace artifact {required_name!r} is missing; refresh the workspace"
            )
        if getattr(required_entry, "kind", None) != ArtifactKind.WORKSPACE:
            raise ValueError(f"required workspace artifact {required_name!r} has the wrong kind")
    summary_name = step_def.get("output_artifact") if step_def is not None else None
    workspace_entries: list[tuple[str, Any, Path]] = []
    for name, entry in artifacts.items():
        if getattr(entry, "kind", None) != ArtifactKind.WORKSPACE:
            continue
        path = Path(str(getattr(entry, "path", entry)))
        if not isinstance(required_name, str) and path.name != "workspace.json":
            # Bounded v0.2 adapter: a mixed code/workspace record remains
            # readable for legacy consumers, but is never certified as a
            # current Git workspace.
            continue
        workspace_entries.append((name, entry, path))

    if not workspace_entries:
        return

    active_repo = Path(root).resolve()
    for name, entry, path in workspace_entries:
        try:
            workspace = WorkspaceArtifact.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, UnicodeError, json.JSONDecodeError, WorkspaceArtifactError) as exc:
            raise ValueError(
                f"workspace artifact {name!r} is missing or malformed; refresh it"
            ) from exc
        if workspace.name != name or getattr(entry, "name", name) != workspace.name:
            raise ValueError(f"workspace artifact {name!r} has contradictory logical identity")
        if summary_name is not None and name == summary_name:
            raise ValueError("workspace artifact must be distinct from the summary artifact")
        if getattr(entry, "version", workspace.version) != workspace.version:
            raise ValueError(f"workspace artifact {name!r} version is contradictory")
        if getattr(entry, "base_sha", workspace.base_sha) != workspace.base_sha:
            raise ValueError(f"workspace artifact {name!r} base SHA is contradictory")
        if getattr(entry, "head_sha", workspace.head_sha) != workspace.head_sha:
            raise ValueError(f"workspace artifact {name!r} head SHA is contradictory")
        if workspace == verified_workspace and workspace.repository == str(active_repo):
            # Reuse only the exact snapshot just checked by refresh in this
            # same leased guard invocation. Every subsequent guard checks
            # Git again, including the immediate second stability check.
            continue
        checked = verify_workspace_artifact(
            workspace,
            repo=active_repo,
        )
        if not checked.valid:
            detail = "; ".join(checked.reasons)
            raise ValueError(
                f"workspace artifact {name!r} is stale or contradictory: {detail}; "
                "refresh it or resolve the conflict before retrying"
            )
