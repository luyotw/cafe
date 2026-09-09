"""User journeys for playbook applicability migration and catalog comparison."""

from pathlib import Path

from typer.testing import CliRunner

from cafe.playbooks.loader import PlaybookLoader
from cafe.ui.cli import app

runner = CliRunner()


def _write_playbook(root: Path, playbook_id: str, *, summary: str | None) -> None:
    applicability = ""
    if summary is not None:
        applicability = f"""
  applicability:
    summary: "{summary}"
    use_when:
      - "The confirmed scope matches this workflow."
    avoid_when:
      - "The confirmed scope needs responsibilities absent from this workflow."
"""
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{playbook_id}.yaml").write_text(
        f"""
playbook:
  id: {playbook_id}{applicability}
skills:
  workflow: {{shared: []}}
  chat: {{shared: []}}
roles:
  developer: {{}}
commands:
  prepare:
    prompt_for_spec_plan_config: false
steps:
  develop:
    skill: cafe-develop
    role: developer
    "on": {{await_agent: _done}}
""".strip(),
        encoding="utf-8",
    )


def test_author_migrates_legacy_custom_playbook_without_graph_edits(
    tmp_path: Path, monkeypatch
) -> None:
    """I1 — inspection warns, strict validation fails, metadata-only migration passes."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "cafe.utils.config.get_global_cafe_dir", lambda: tmp_path / "global"
    )
    root = tmp_path / ".cafe" / "playbooks"
    _write_playbook(root, "custom", summary=None)
    loader = PlaybookLoader(project_root=tmp_path)
    graph_before = loader.load_model("custom").model.steps["develop"].model_dump()

    inspected = runner.invoke(app, ["playbook", "show", "custom"])
    rejected = runner.invoke(app, ["playbook", "validate", "custom", "--strict"])

    assert inspected.exit_code == 0
    assert "ineligible" in inspected.stdout
    assert rejected.exit_code == 1
    assert "playbook.applicability" in rejected.stdout

    _write_playbook(root, "custom", summary="A migrated custom implementation workflow.")
    accepted = runner.invoke(app, ["playbook", "validate", "custom", "--strict"])
    migrated = PlaybookLoader(project_root=tmp_path).load_model("custom", strict=True)

    assert accepted.exit_code == 0
    assert migrated.model.steps["develop"].model_dump() == graph_before
