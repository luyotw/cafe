"""U9–U11/I6: confinement, stale previews, rollback and recovery."""

from pathlib import Path

import pytest

from cafe.authoring import apply, decode_request, prepare
from cafe.authoring.transaction import storage

FIXTURE = Path(__file__).parents[1] / "fixtures/authoring/pair.yaml"


def pair():
    return decode_request(FIXTURE.read_text())


@pytest.mark.parametrize(
    "target",
    [
        "/tmp/SKILL.md",
        ".cafe/issues/x/SKILL.md",
        ".codex/skills/cafe-observe/SKILL.md",
        ".cafe/skills/../issues/x/SKILL.md",
        "src/cafe/data/skills/cafe-observe/SKILL.md",
    ],
)
def test_forbidden_targets_do_not_publish(tmp_path, target):
    request = pair()["companions"][0]
    request["target"] = target
    assert apply(request, root=tmp_path).status == "rejected"
    assert not (tmp_path / ".cafe").exists()


def test_symlink_ancestor_is_refused(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".cafe").symlink_to(outside, target_is_directory=True)
    assert apply(pair(), root=tmp_path).status == "rejected"
    assert list(outside.iterdir()) == []


def test_handled_publication_failure_restores_entire_write_set(tmp_path, monkeypatch):
    from cafe.authoring import transaction

    original = transaction.publish
    count = 0

    def fail_second(root, relative, content, mode):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("injected publication boundary failure")
        return original(root, relative, content, mode)

    monkeypatch.setattr(transaction, "publish", fail_second)
    result = apply(pair(), root=tmp_path)
    assert result.status == "rejected"
    assert not (tmp_path / ".cafe").exists()
    assert not (storage(tmp_path) / "pending.json").exists()


def test_interrupted_apply_recovers_before_later_apply(tmp_path, monkeypatch):
    from cafe.authoring import transaction

    original = transaction.publish
    count = 0

    def interrupt_second(root, relative, content, mode):
        nonlocal count
        count += 1
        if count == 2:
            raise KeyboardInterrupt()
        return original(root, relative, content, mode)

    monkeypatch.setattr(transaction, "publish", interrupt_second)
    with pytest.raises(KeyboardInterrupt):
        apply(pair(), root=tmp_path)
    assert (storage(tmp_path) / "pending.json").exists()
    preview = prepare(pair(), root=tmp_path)
    assert any(d["code"] == "pending_recovery" for d in preview.diagnostics)
    monkeypatch.setattr(transaction, "publish", original)
    assert apply(pair(), root=tmp_path).status == "applied"


def test_recovery_preserves_concurrent_changes(tmp_path, monkeypatch):
    from cafe.authoring import transaction

    original = transaction.publish
    count = 0

    def interrupt_second(root, relative, content, mode):
        nonlocal count
        count += 1
        if count == 2:
            raise KeyboardInterrupt()
        return original(root, relative, content, mode)

    monkeypatch.setattr(transaction, "publish", interrupt_second)
    with pytest.raises(KeyboardInterrupt):
        apply(pair(), root=tmp_path)
    path = tmp_path / pair()["target"]
    path.write_text("concurrent change\n")
    monkeypatch.setattr(transaction, "publish", original)
    assert apply(pair(), root=tmp_path).status == "rejected"
    assert path.read_text() == "concurrent change\n"
    assert (storage(tmp_path) / "pending.json").exists()


def test_dependency_mutation_invalidates_reviewed_preview(tmp_path):
    request = pair()
    assert apply(request, root=tmp_path).status == "applied"
    request = {
        "version": 1,
        "mode": "patch",
        "target": request["target"],
        "operations": [
            {"op": "upsert", "path": ["steps", "observe", "allowed_goto"], "value": "observe"}
        ],
    }
    preview = prepare(request, root=tmp_path)
    path = tmp_path / ".cafe/skills/cafe-observe/SKILL.md"
    path.write_text(path.read_text().replace("provenance.", "provenance carefully."))
    assert apply(request, root=tmp_path, expect_change=preview.change_digest).status == "rejected"
    assert "allowed_goto" not in (tmp_path / request["target"]).read_text()


def test_final_validation_failure_restores_original_modes(tmp_path, monkeypatch):
    import os
    import stat

    import cafe.authoring as authoring

    request = pair()
    assert apply(request, root=tmp_path).status == "applied"
    book = tmp_path / request["target"]
    os.chmod(book, 0o640)
    original = book.read_bytes()
    patch = {
        "version": 1,
        "target": request["target"],
        "mode": "patch",
        "operations": [
            {"op": "upsert", "path": ["steps", "observe", "allowed_goto"], "value": "observe"}
        ],
    }
    original_prepare = authoring.prepare

    def fail_published(request, **kwargs):
        result = original_prepare(request, **kwargs)
        if (storage(tmp_path) / "pending.json").exists():
            result.diagnose("injected_validation", "Filesystem publication check failed")
        return result

    monkeypatch.setattr(authoring, "prepare", fail_published)
    result = authoring.apply(patch, root=tmp_path)
    assert result.status == "rejected"
    assert book.read_bytes() == original
    assert stat.S_IMODE(book.stat().st_mode) == 0o640


def test_authorized_builtin_patch_and_project_shadow_are_reported(tmp_path):
    import subprocess

    core = tmp_path / "src/cafe/core/playbook.py"
    core.parent.mkdir(parents=True)
    core.write_text("# CAFE source fixture\n")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "cafe-engine"\n')
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "pyproject.toml", "src/cafe/core/playbook.py"],
        check=True,
    )
    request = pair()["companions"][0]
    request["target"] = "src/cafe/data/skills/cafe-observe/SKILL.md"
    assert apply(request, root=tmp_path).status == "applied"
    source = tmp_path / request["target"]
    before = source.read_text()
    patch = {
        "version": 1,
        "target": request["target"],
        "mode": "patch",
        "operations": [
            {"op": "upsert", "path": ["metadata", "workflow", "required_tools"], "value": []}
        ],
    }
    assert apply(patch, root=tmp_path).status == "applied"
    assert source.read_text().split("---\n", 2)[2] == before.split("---\n", 2)[2]
    shadow = tmp_path / ".cafe/skills/cafe-observe/SKILL.md"
    shadow.parent.mkdir(parents=True)
    shadow.write_text(source.read_text())
    result = prepare(patch, root=tmp_path)
    assert any(d["code"] == "ineffective_shadow" for d in result.diagnostics)
