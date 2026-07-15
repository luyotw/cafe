from pathlib import Path

import yaml

import pytest

from cafe.utils.phase_config import load_phase_step_model


def _write_yaml(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")


def test_load_phase_step_model_prefers_worktree_then_repo(tmp_path: Path) -> None:
    local_phase_config = tmp_path / "worktree" / ".cafe" / "phases.yaml"
    repo_phase_config = tmp_path / ".cafe" / "phases.yaml"

    _write_yaml(repo_phase_config, {"build": {"name": "RepoName", "clis": [{"cli": "claude", "model": "repo"}]}})
    _write_yaml(local_phase_config, {"build": {"clis": [{"cli": "gemini", "model": "local-gemini"}]}})

    result = load_phase_step_model(
        step_name="build",
        local_path=local_phase_config,
        repo_path=repo_phase_config,
    )

    assert result.name == "RepoName"
    assert result.clis == (("gemini", "local-gemini"),)
    assert result.model == "local-gemini"
    assert result.chain == ("worktree", "repo")


def test_load_phase_step_model_rejects_invalid_payload_shape(tmp_path: Path) -> None:
    phase_config = tmp_path / ".cafe" / "phases.yaml"
    _write_yaml(phase_config, {"build": {"unsupported": "value"}})
    with pytest.raises(ValueError, match="unknown field"):
        load_phase_step_model(step_name="build", local_path=phase_config)


def test_load_phase_step_model_rejects_duplicate_cli_entries(tmp_path: Path) -> None:
    phase_config = tmp_path / ".cafe" / "phases.yaml"
    _write_yaml(
        phase_config,
        {"build": {"clis": [{"cli": "claude", "model": "opus"}, {"cli": "claude", "model": "sonnet"}]}},
    )
    with pytest.raises(ValueError, match="duplicate cli"):
        load_phase_step_model(step_name="build", local_path=phase_config)


def test_load_phase_step_model_rejects_unknown_cli(tmp_path: Path) -> None:
    phase_config = tmp_path / ".cafe" / "phases.yaml"
    _write_yaml(
        phase_config,
        {"build": {"clis": [{"cli": "not-a-cli", "model": "bad"}]}},
    )
    with pytest.raises(ValueError, match="unsupported cli"):
        load_phase_step_model(step_name="build", local_path=phase_config)


def test_load_phase_step_model_field_by_field_fallback(tmp_path: Path) -> None:
    local = tmp_path / "worktree" / ".cafe" / "phases.yaml"
    repo = tmp_path / ".cafe" / "phases.yaml"
    _write_yaml(local, {"build": {"clis": [{"cli": "codex", "model": "local-codex"}]}})
    _write_yaml(repo, {"build": {"name": "RepoName", "role": "developer", "clis": [{"cli": "claude", "model": "repo-claude"}]}})

    result = load_phase_step_model(step_name="build", local_path=local, repo_path=repo)
    assert result.name == "RepoName"
    assert result.role == "developer"
    assert result.model == "local-codex"
