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


@pytest.mark.parametrize("state", ["staged", "unstaged", "untracked", "ignored", "tracked-ignored", "deleted", "renamed", "mixed"])
def test_u1_u2_inspection_and_snapshot_share_cleanliness(tmp_path, state):
    from cafe.core.workspace_artifact import DirtyWorkspaceError, inspect_workspace

    repo, base, head = _repo(tmp_path)
    snapshot = build_workspace_artifact(repo=repo, name="snapshot", version=1, base_sha=base, head_sha=head)
    before_head = _git(repo, "rev-parse", "HEAD")
    if state in ("staged", "unstaged", "tracked-ignored", "mixed"):
        (repo / "tracked.txt").write_text("changed\n")
    if state in ("staged", "mixed"):
        _git(repo, "add", "tracked.txt")
    if state in ("untracked", "mixed"):
        (repo / "空 白.txt").write_text("new")
    if state in ("ignored", "tracked-ignored"):
        with (repo / ".gitignore").open("a") as stream:
            stream.write("*.txt\n")
        _git(repo, "add", ".gitignore")
        _git(repo, "commit", "-m", "ignore")
        head = _git(repo, "rev-parse", "HEAD")
        (repo / "ignored.txt").write_text("ignored")
    if state == "deleted":
        (repo / "tracked.txt").unlink()
    if state == "renamed":
        _git(repo, "mv", "tracked.txt", "改 名.txt")
    before = _git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    inspection = inspect_workspace(repo)
    assert inspection.clean == (state == "ignored")
    if inspection.clean:
        build_workspace_artifact(repo=repo, name="snapshot", version=1, base_sha=base, head_sha=head)
    else:
        with pytest.raises(DirtyWorkspaceError) as rejected:
            build_workspace_artifact(repo=repo, name="snapshot", version=1, base_sha=base, head_sha=head)
        assert rejected.value.changes == inspection.changes
        assert not verify_workspace_artifact(snapshot, repo=repo).valid
        if state == "renamed":
            assert inspection.changes[0]["path"] == "改 名.txt"
            assert inspection.changes[0]["old_path"] == "tracked.txt"
        if state == "untracked":
            assert inspection.changes[0]["path"] == "空 白.txt"
            assert inspection.changes[0]["state"] == "??"
    assert _git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all") == before
    assert _git(repo, "rev-parse", "HEAD") == (head if state in ("ignored", "tracked-ignored") else before_head)


def test_u2_other_workspace_errors_are_not_dirty(tmp_path):
    from cafe.core.workspace_artifact import DirtyWorkspaceError, inspect_workspace

    with pytest.raises(WorkspaceArtifactError) as rejected:
        inspect_workspace(tmp_path)
    assert not isinstance(rejected.value, DirtyWorkspaceError)
    repo, base, head = _repo(tmp_path)
    (repo / "new.txt").write_text("new")
    with pytest.raises(WorkspaceArtifactError) as rejected:
        build_workspace_artifact(repo=repo, name="snapshot", version=1, base_sha="bad-ref", head_sha=head)
    assert not isinstance(rejected.value, DirtyWorkspaceError)


def test_u5_dirty_feedback_preserves_ownership_and_authority(tmp_path):
    from cafe.core.workspace_artifact import DirtyWorkspaceError, inspect_workspace

    repo, _, _ = _repo(tmp_path)
    (repo / "owner.txt").write_text("pre-existing")
    error = DirtyWorkspaceError(inspect_workspace(repo).changes)
    prompt = error.correction_prompt(consumed=1, remaining=2)
    assert "owner.txt" in prompt and "??" in prompt
    for meaning in ("origin", "pre-existing", "authorization", "clarification", "permission", "1", "2"):
        assert meaning in prompt
