"""Unit coverage for the declared Git workspace snapshot."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from cafe.core.workspace_artifact import (
    WorkspaceArtifact,
    WorkspaceArtifactError,
    build_workspace_artifact,
    verify_workspace_artifact,
)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _repo(tmp_path: Path) -> tuple[Path, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / ".gitignore").write_text(".cafe/\n", encoding="utf-8")
    (repo / "tracked.txt").write_text("before\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "tracked.txt").write_text("after\n", encoding="utf-8")
    (repo / "added.txt").write_text("added\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "change")
    return repo, base, _git(repo, "rev-parse", "HEAD")


def test_workspace_snapshot_binds_exact_changed_files(tmp_path: Path) -> None:
    repo, base, head = _repo(tmp_path)

    artifact = build_workspace_artifact(
        repo=repo,
        name="verified_snapshot",
        version=1,
        base_sha=base,
        head_sha=head,
    )

    assert {item["path"] for item in artifact.changed_files} == {"added.txt", "tracked.txt"}
    assert artifact.base_sha == base
    assert artifact.head_sha == head
    assert "receipts" not in artifact.to_dict()
    assert verify_workspace_artifact(artifact, repo=repo).valid is True


def test_workspace_snapshot_ignores_legacy_receipt_metadata(tmp_path: Path) -> None:
    repo, base, head = _repo(tmp_path)
    artifact = build_workspace_artifact(
        repo=repo,
        name="snapshot",
        version=1,
        base_sha=base,
        head_sha=head,
        receipt_outputs=[repo / "missing" / "output.md"],
    )
    legacy = artifact.to_dict()
    legacy["receipts"] = [{"malformed": "legacy metadata is ignored"}]

    restored = WorkspaceArtifact.from_dict(legacy)

    assert restored == artifact
    assert verify_workspace_artifact(restored, repo=repo).valid is True


def test_workspace_snapshot_rejects_stale_changed_files_or_head(tmp_path: Path) -> None:
    repo, base, head = _repo(tmp_path)
    artifact = build_workspace_artifact(
        repo=repo,
        name="snapshot",
        version=1,
        base_sha=base,
        head_sha=head,
    )
    raw = artifact.to_dict()
    raw["changed_files"] = raw["changed_files"][:-1]
    stale = WorkspaceArtifact.from_dict(raw)

    checked = verify_workspace_artifact(stale, repo=repo)
    assert checked.valid is False
    assert any("changed-file" in reason for reason in checked.reasons)

    (repo / "later.txt").write_text("later\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "later")
    checked = verify_workspace_artifact(artifact, repo=repo)
    assert checked.valid is False
    assert "workspace head is stale" in checked.reasons


def test_workspace_snapshot_rejects_dirty_worktree(tmp_path: Path) -> None:
    repo, base, head = _repo(tmp_path)
    (repo / "tracked.txt").write_text("uncommitted\n", encoding="utf-8")

    with pytest.raises(WorkspaceArtifactError, match="worktree is dirty"):
        build_workspace_artifact(
            repo=repo,
            name="snapshot",
            version=1,
            base_sha=base,
            head_sha=head,
        )


def test_workspace_snapshot_rejects_reversed_or_unsupported_records(tmp_path: Path) -> None:
    repo, base, head = _repo(tmp_path)
    with pytest.raises(WorkspaceArtifactError, match="ancestor"):
        build_workspace_artifact(
            repo=repo,
            name="snapshot",
            version=1,
            base_sha=head,
            head_sha=base,
        )

    with pytest.raises(WorkspaceArtifactError, match="schema"):
        WorkspaceArtifact.from_dict({"schema_version": 99})
