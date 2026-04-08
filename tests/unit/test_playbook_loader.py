"""Tests for playbook loader."""

from pathlib import Path

import pytest

from cafe.playbooks.loader import PlaybookLoader


def _write_playbook(root: Path, name: str, content: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{name}.yaml").write_text(content, encoding="utf-8")


def test_load_uses_project_override(tmp_path: Path) -> None:
    builtin_root = tmp_path / "builtin" / "playbooks"
    global_root = tmp_path / "global" / "playbooks"
    project_root = tmp_path / "project" / ".cafe" / "playbooks"

    pb = """
playbook: {id: default}
steps:
  spec:
    role: pm
    skill: spec_first
    valid_status_codes: [CAFE_CONFIRMED]
    on: {CAFE_CONFIRMED: plan}
"""
    _write_playbook(builtin_root, "default", pb)
    _write_playbook(global_root, "default", pb.replace("pm", "dev"))
    _write_playbook(project_root, "default", pb.replace("pm", "reviewer"))

    loader = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=tmp_path / "builtin",
    )
    result = loader.load("default")
    assert result["steps"]["spec"]["role"] == "reviewer"


def test_load_invalid_schema_raises(tmp_path: Path) -> None:
    builtin_root = tmp_path / "builtin" / "playbooks"
    _write_playbook(
        builtin_root,
        "bad",
        """
playbook: {id: bad}
steps:
  spec:
    role: pm
""",
    )
    loader = PlaybookLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=tmp_path / "builtin",
    )
    with pytest.raises(ValueError, match="missing skill"):
        loader.load("bad")
