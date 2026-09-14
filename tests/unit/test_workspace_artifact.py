"""Unit coverage for the declared verified workspace companion."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from cafe.core.workspace_artifact import (
    WorkspaceArtifact,
    WorkspaceArtifactError,
    build_workspace_artifact,
    verify_workspace_artifact,
)
from cafe.verification import run_verification


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
    base = _git(repo, "add", ".") or _git(repo, "commit", "-m", "initial")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "tracked.txt").write_text("after\n", encoding="utf-8")
    (repo / "added.txt").write_text("added\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "change")
    return repo, base, _git(repo, "rev-parse", "HEAD")


def _receipt(repo: Path) -> Path:
    output = repo / ".cafe/issues/issue/develop/iteration_001/output.md"
    output.parent.mkdir(parents=True)
    run_verification(
        output_file=output,
        command=[sys.executable, "-c", "print('workspace evidence')"],
        scope="targeted",
        cwd=repo,
    )
    return output


def test_workspace_snapshot_binds_exact_changed_files_and_receipt(tmp_path: Path) -> None:
    repo, base, head = _repo(tmp_path)
    receipt = _receipt(repo)

    artifact = build_workspace_artifact(
        repo=repo,
        name="verified_snapshot",
        version=1,
        base_sha=base,
        head_sha=head,
        receipt_outputs=[receipt],
    )

    assert {item["path"] for item in artifact.changed_files} == {"added.txt", "tracked.txt"}
    assert artifact.base_sha == base
    assert artifact.head_sha == head
    assert verify_workspace_artifact(artifact, repo=repo).valid is True


def test_workspace_snapshot_rejects_stale_changed_files_or_receipts(tmp_path: Path) -> None:
    repo, base, head = _repo(tmp_path)
    receipt = _receipt(repo)
    artifact = build_workspace_artifact(
        repo=repo,
        name="snapshot",
        version=1,
        base_sha=base,
        head_sha=head,
        receipt_outputs=[receipt],
    )
    raw = artifact.to_dict()
    raw["changed_files"] = raw["changed_files"][:-1]
    stale = WorkspaceArtifact.from_dict(raw)

    checked = verify_workspace_artifact(stale, repo=repo)
    assert checked.valid is False
    assert any("changed-file" in reason for reason in checked.reasons)

    raw_receipt = json.loads(
        (receipt.parent / "verification.json").read_text(encoding="utf-8")
    )
    raw_receipt["git"]["head"] = base
    (receipt.parent / "verification.json").write_text(
        json.dumps(raw_receipt), encoding="utf-8"
    )
    assert verify_workspace_artifact(artifact, repo=repo).valid is False


def test_workspace_snapshot_rejects_reversed_or_unsupported_records(tmp_path: Path) -> None:
    repo, base, head = _repo(tmp_path)
    with pytest.raises(WorkspaceArtifactError, match="ancestor"):
        build_workspace_artifact(
            repo=repo,
            name="snapshot",
            version=1,
            base_sha=head,
            head_sha=base,
            receipt_outputs=[],
        )

    with pytest.raises(WorkspaceArtifactError, match="schema"):
        WorkspaceArtifact.from_dict({"schema_version": 99})
