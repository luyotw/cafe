"""User journeys for playbook applicability migration and catalog comparison."""

from pathlib import Path

from typer.testing import CliRunner

from cafe.playbooks.loader import PlaybookLoader
from cafe.playbooks.simulate import analyze_playbook
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


def test_operator_compares_each_effective_candidate_once(
    tmp_path: Path, monkeypatch
) -> None:
    """I2 — project precedence controls both applicability and displayed source."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "cafe.utils.config.get_global_cafe_dir", lambda: tmp_path / "global"
    )
    _write_playbook(
        tmp_path / "global" / "playbooks",
        "custom",
        summary="A shadowed Global workflow.",
    )
    _write_playbook(
        tmp_path / ".cafe" / "playbooks",
        "custom",
        summary="The effective project workflow.",
    )

    listed = runner.invoke(app, ["playbook", "list"])
    shown = runner.invoke(app, ["playbook", "show", "custom"])

    assert listed.exit_code == 0
    assert listed.stdout.count("The effective project workflow.") == 1
    assert "A shadowed Global workflow." not in listed.stdout
    assert shown.exit_code == 0
    assert "The effective project workflow." in shown.stdout
    assert "source=project" in shown.stdout


def test_operator_inspects_strict_bundled_selection_choices(
    tmp_path: Path, monkeypatch
) -> None:
    """I3 — packaged candidates remain valid, complete, and behaviorally unchanged."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "cafe.utils.config.get_global_cafe_dir", lambda: tmp_path / "global"
    )
    loader = PlaybookLoader(project_root=tmp_path)
    ids = loader.list_playbooks()

    listed = runner.invoke(app, ["playbook", "list"])
    shown = runner.invoke(app, ["playbook", "show", "standard"])

    assert listed.exit_code == 0
    assert shown.exit_code == 0
    assert "Applicability" in shown.stdout
    for playbook_id in ids:
        loaded = loader.load_model(playbook_id, strict=True)
        assert loaded.automatic_selection_eligible is True
        assert loaded.model.playbook.applicability is not None
        analysis = analyze_playbook(loaded.model)
        assert analysis.unreachable_steps == ()
        assert analysis.dead_end_steps == ()
