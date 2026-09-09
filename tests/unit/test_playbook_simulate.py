"""Tests for ``playbook simulate`` static graph analysis."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from cafe.playbooks.loader import PlaybookLoader
from cafe.playbooks.simulate import analyze_playbook, format_dot, format_text_report
from cafe.ui.cli import app

pytestmark = pytest.mark.usefixtures("cached_builtin_playbook_models")

runner = CliRunner()


def _write_playbook(root: Path, stem: str, content: str) -> None:
    pb_dir = root / ".cafe" / "playbooks"
    pb_dir.mkdir(parents=True, exist_ok=True)
    (pb_dir / f"{stem}.yaml").write_text(content, encoding="utf-8")


def test_simulate_unknown_playbook_no_transition_report(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["playbook", "simulate", "does_not_exist_252"])
    assert result.exit_code == 1
    assert "Error:" in result.stdout
    assert "Transitions (intent -> next step)" not in result.stdout


def test_simulate_bad_entry_point_after_load(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _write_playbook(
        tmp_path,
        "sim_bad_entry",
        """
playbook:
  id: sim_bad_entry
  name: Bad entry
roles:
  pm:
    description: PM
steps:
  only:
    type: skill
    skill: spec_first
    role: pm
    on:
      await_agent: _done
entry_point: not_a_step
""".strip()
        + "\n",
    )
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["playbook", "simulate", "sim_bad_entry"])
    assert result.exit_code == 1
    assert "entry_point" in result.stdout
    assert "Transitions (intent -> next step)" not in result.stdout


def test_simulate_cycle_detection(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _write_playbook(
        tmp_path,
        "sim_cycle",
        """
playbook:
  id: sim_cycle
  name: Sim cycle
roles:
  pm:
    description: PM
steps:
  a:
    type: skill
    skill: spec_first
    role: pm
    on:
      await_agent: b
  b:
    type: skill
    skill: spec_first
    role: pm
    on:
      await_agent: a
entry_point: a
""".strip()
        + "\n",
    )
    monkeypatch.chdir(tmp_path)
    loader = PlaybookLoader(project_root=tmp_path)
    res = analyze_playbook(loader.load_model("sim_cycle").model)
    assert res.cycles
    assert "a" in res.cycles[0] and "b" in res.cycles[0]


def test_simulate_unreachable_step(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _write_playbook(
        tmp_path,
        "sim_unreach",
        """
playbook:
  id: sim_unreach
  name: Sim unreachable
roles:
  pm:
    description: PM
steps:
  a:
    type: skill
    skill: spec_first
    role: pm
    on:
      await_agent: _done
  orphan:
    type: skill
    skill: spec_first
    role: pm
    on:
      await_agent: orphan
entry_point: a
""".strip()
        + "\n",
    )
    monkeypatch.chdir(tmp_path)
    res = analyze_playbook(PlaybookLoader(project_root=tmp_path).load_model("sim_unreach").model)
    assert "orphan" in res.unreachable_steps


def test_simulate_dead_end_step(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _write_playbook(
        tmp_path,
        "sim_dead",
        """
playbook:
  id: sim_dead
  name: Sim dead end
roles:
  pm:
    description: PM
steps:
  live:
    type: skill
    skill: spec_first
    role: pm
    on:
      await_agent: sink
  sink:
    type: skill
    skill: spec_first
    role: pm
    on: {}
entry_point: live
""".strip()
        + "\n",
    )
    monkeypatch.chdir(tmp_path)
    res = analyze_playbook(PlaybookLoader(project_root=tmp_path).load_model("sim_dead").model)
    assert "sink" in res.dead_end_steps


def test_simulate_missing_handler_from_valid_intents(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _write_playbook(
        tmp_path,
        "sim_bad_intent",
        """
playbook:
  id: sim_bad_intent
  name: Sim bad intent
roles:
  pm:
    description: PM
steps:
  only:
    type: skill
    skill: spec_first
    role: pm
    valid_intents: [ready_for_review]
    on:
      await_agent: _done
entry_point: only
""".strip()
        + "\n",
    )
    monkeypatch.chdir(tmp_path)
    res = analyze_playbook(PlaybookLoader(project_root=tmp_path).load_model("sim_bad_intent").model)
    assert res.missing_intent_handlers
    assert "confirm_output" in res.missing_intent_handlers[0]


def test_simulate_builtin_default_and_dot(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    monkeypatch.chdir(repo)
    result = runner.invoke(app, ["playbook", "simulate", "standard", "--dot"])
    assert result.exit_code == 0
    out = result.stdout
    assert "Transitions (intent -> next step)" in out
    assert "Unreachable steps (from entry)" in out
    assert "(no findings)" in out
    assert "digraph playbook" in out


def test_format_dot_edge_count_matches_model(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _write_playbook(
        tmp_path,
        "sim_tiny",
        """
playbook:
  id: sim_tiny
  name: Sim tiny
roles:
  pm:
    description: PM
steps:
  only:
    type: skill
    skill: spec_first
    role: pm
    on:
      await_agent: _done
entry_point: only
""".strip()
        + "\n",
    )
    monkeypatch.chdir(tmp_path)
    model = PlaybookLoader(project_root=tmp_path).load_model("sim_tiny").model
    res = analyze_playbook(model)
    dot = format_dot(res)
    assert dot.count(" -> ") == len(res.edges)
    text = format_text_report(res)
    assert "Dead-end steps" in text
    assert "_done" in text


def test_simulate_simple_builtin(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    monkeypatch.chdir(repo)
    r = runner.invoke(app, ["playbook", "simulate", "simple"])
    assert r.exit_code == 0
    assert "Entry point: spec" in r.stdout
