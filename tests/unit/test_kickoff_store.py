"""U04/U06: recoverable kickoff records and repository identity."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _kickoff_test_support import load_kickoff_module


def test_store_rejects_bad_schema_and_corrupt_records_independently(tmp_path: Path) -> None:
    module = load_kickoff_module("_kickoff_store")
    path = tmp_path / "preferences.json"
    store = module.VersionedJsonStore(path, schema_version=1, collection="records")
    store.write({"good": {"value": "zh-TW"}})
    path.write_text('{"schema_version": 2, "records": {"bad": {}}}', encoding="utf-8")

    assert store.read() == {}
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "records": {"good": {"value": "zh-TW"}, "bad": None},
            }
        ),
        encoding="utf-8",
    )
    assert store.read() == {"good": {"value": "zh-TW"}}


def test_failed_atomic_replace_preserves_previous_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = load_kickoff_module("_kickoff_store")
    path = tmp_path / "preferences.json"
    store = module.VersionedJsonStore(path, schema_version=1, collection="records")
    store.write({"language": {"value": "en-US"}})

    def fail_replace(*_args: object) -> None:
        raise OSError("replacement failed")

    monkeypatch.setattr(module.os, "replace", fail_replace)
    with pytest.raises(module.StoreError):
        store.write({"language": {"value": "zh-TW"}})

    monkeypatch.undo()
    assert store.read() == {"language": {"value": "en-US"}}
    assert not list(tmp_path.glob("*.tmp"))


def test_repository_identity_shares_linked_worktrees_only(tmp_path: Path) -> None:
    module = load_kickoff_module("_kickoff_store")
    main = tmp_path / "main"
    linked = tmp_path / "linked"
    clone = tmp_path / "clone"
    for root in (main, clone):
        root.mkdir()
        subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(main), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(main), "config", "user.name", "Test"], check=True)
    (main / "tracked.txt").write_text("test\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(main), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(main), "commit", "-qm", "initial"], check=True)
    subprocess.run(["git", "-C", str(main), "worktree", "add", "--detach", "-q", str(linked), "HEAD"], check=True)

    assert module.repository_identity(main) == module.repository_identity(linked)
    assert module.repository_identity(main) != module.repository_identity(clone)
    assert module.repository_identity(tmp_path / "plain") == module.repository_identity(
        tmp_path / "plain"
    )
