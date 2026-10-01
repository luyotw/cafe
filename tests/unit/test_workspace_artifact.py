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


def test_u5_workspace_prompt_has_complete_authored_locale_templates():
    from string import Formatter

    from cafe.core.runtime_locales import load_catalogs

    catalogs = load_catalogs()
    assert set(catalogs) == {"en-US", "zh-TW"}
    for catalog in catalogs.values():
        template = catalog["workspace.correction"]
        fields = {field for _, field, _, _ in Formatter().parse(template) if field is not None}
        assert fields == {"reason", "consumed", "remaining"}


@pytest.mark.parametrize(
    "locale,authored_locale",
    [
        ("en-US", "en-US"),
        ("zh-TW", "zh-TW"),
        ("zh-Hant", "zh-TW"),
        ("zh-HK", "zh-TW"),
        ("zh-MO", "zh-TW"),
        (" ZH_hant_hk ", "zh-TW"),
        ("zh-CN", "en-US"),
        ("zh-Hans", "en-US"),
        ("zh-Hans-TW", "en-US"),
        ("zh", "en-US"),
        ("fr-FR", "en-US"),
        (None, "en-US"),
        ("", "en-US"),
        ("auto", "en-US"),
        ("zh//TW", "en-US"),
    ],
)
def test_u5_workspace_prompt_locale_fallback_and_literal_interpolation(locale, authored_locale):
    from cafe.core.workspace_artifact import workspace_correction_prompt

    reason = "?? owner/{consumed} 空 白.txt; literal {remaining}"
    prompt = workspace_correction_prompt(reason, consumed=17, remaining=3, locale=locale)
    assert prompt == workspace_correction_prompt(
        reason, consumed=17, remaining=3, locale=authored_locale
    )
    assert reason in prompt and "17" in prompt and "3" in prompt


@pytest.mark.parametrize(
    "locale,meanings",
    [
        ("en-US", ("origin", "pre-existing", "authorization", "clarification", "permission",
                   "human answer", "phase", "iteration", "CLI", "model", "session", "tools",
                   "directories", "permissions", "output", "checklist", "evidence", "handoff",
                   "stage", "commit", "stash", "restore", "delete")),
        ("zh-TW", ("來源", "已有的工作", "授權", "澄清", "權限", "人的回答", "phase", "iteration",
                   "CLI", "model", "session", "工具", "目錄", "成果", "checklist", "evidence", "handoff",
                   "暫存", "提交", "stash", "還原", "刪除")),
    ],
)
def test_u5_authored_prompt_preserves_full_correction_instructions(locale, meanings):
    from cafe.core.workspace_artifact import workspace_correction_prompt

    prompt = workspace_correction_prompt("owner.txt", consumed=1, remaining=2, locale=locale)
    assert all(meaning in prompt for meaning in meanings)


@pytest.mark.parametrize("locale", ["en-US", "zh-TW"])
@pytest.mark.parametrize("bytes_count", [8192, 8193, 1_048_577])
def test_u5_prompt_interpolation_keeps_utf8_diagnostics_bounded(locale, bytes_count):
    from cafe.core.workspace_artifact import bounded_workspace_reason, workspace_correction_prompt

    prefix = "?? 空 白/{reason}.txt; "
    reason = prefix + "界" * ((bytes_count - len(prefix.encode())) // 3)
    reason += "x" * (bytes_count - len(reason.encode()))
    prompt = workspace_correction_prompt(reason, consumed=1, remaining=2, locale=locale)
    bounded = bounded_workspace_reason(reason)
    assert bounded in prompt and prefix in prompt
    assert len(bounded.encode()) <= 8192 and len(prompt.encode()) < 10000
    if bytes_count <= 8192:
        assert reason in prompt
    else:
        assert reason not in prompt and str(bytes_count) in prompt
