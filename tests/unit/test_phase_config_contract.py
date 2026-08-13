import inspect

import pytest

from cafe.utils.phase_config import load_phase_step_model
from cafe.utils.phase_config import model_from_cli_clis


def test_load_phase_step_model_with_direct_root_shape(tmp_path):
    config = tmp_path / "phases.yaml"
    config.write_text(
        """
build:
  name: Build step
  role: developer
  clis:
    - cli: claude
      model: gpt-5
    - cli: gemini
      model: gemini-2.5-pro
""",
        encoding="utf-8",
    )

    result = load_phase_step_model(step_name="build", local_path=config)

    assert result.name == "Build step"
    assert result.role == "developer"
    assert result.clis == (("claude", "gpt-5"), ("gemini", "gemini-2.5-pro"))
    assert result.model == "gpt-5"


def test_load_phase_step_model_rejects_unknown_top_level_fields(tmp_path):
    config = tmp_path / "phases.yaml"
    config.write_text(
        """
build:
  unknown: invalid
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_phase_step_model(step_name="build", local_path=config)


def test_phase_config_malformed_yaml_error_has_validation_context(tmp_path):
    config = tmp_path / "phases.yaml"
    config.write_text("build:\n  clis:\n    - cli: [codex\n", encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        load_phase_step_model(step_name="build", local_path=config)

    message = str(exc_info.value)
    assert config.as_posix() in message
    assert "step='unknown'" in message
    assert "field='document'" in message


def test_phase_config_root_type_error_has_validation_context(tmp_path):
    config = tmp_path / "phases.yaml"
    config.write_text("- spec\n", encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        load_phase_step_model(step_name="build", local_path=config)

    message = str(exc_info.value)
    assert config.as_posix() in message
    assert "step='unknown'" in message
    assert "field='root'" in message


def test_model_from_cli_clis_prefers_first_model_entry():
    assert model_from_cli_clis((("claude", "opus"), ("gemini", "sonnet"))) == "opus"


def test_load_phase_step_model_rejects_empty_phase_entry(tmp_path):
    config = tmp_path / "phases.yaml"
    config.write_text("build: {}\n", encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        load_phase_step_model(step_name="build", local_path=config)

    message = str(exc_info.value)
    assert config.as_posix() in message
    assert "step='build'" in message
    assert "field='build.clis'" in message


def test_load_phase_step_model_rejects_missing_step_with_source_context(tmp_path):
    config = tmp_path / "phases.yaml"
    config.write_text("spec:\n  clis:\n    - cli: codex\n      model: gpt-5\n", encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        load_phase_step_model(step_name="build", local_path=config)

    message = str(exc_info.value)
    assert config.as_posix() in message
    assert "step='build'" in message
    assert "field='build'" in message


def test_load_phase_step_model_rejects_cli_without_exact_model(tmp_path):
    config = tmp_path / "phases.yaml"
    config.write_text("build:\n  clis:\n    - cli: codex\n", encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        load_phase_step_model(step_name="build", local_path=config)

    message = str(exc_info.value)
    assert config.as_posix() in message
    assert "field='build.clis[0].model'" in message


def test_load_phase_step_model_never_reads_legacy_worktree_path(tmp_path):
    legacy = tmp_path / "crew.yaml"
    legacy.write_text("build:\n  clis:\n    - cli: codex\n      model: gpt-5\n", encoding="utf-8")

    assert "worktree_path" not in inspect.signature(load_phase_step_model).parameters

    with pytest.raises(ValueError) as exc_info:
        load_phase_step_model(step_name="build", local_path=None)

    assert legacy.as_posix() not in str(exc_info.value)
    assert "step='build'" in str(exc_info.value)


def test_load_phase_step_model_resolves_missing_fields_field_by_field(tmp_path):
    repo = tmp_path / "repo_phases.yaml"
    worktree = tmp_path / "worktree_phases.yaml"
    repo.write_text(
        """
build:
  name: RepoName
  role: developer
  clis:
    - cli: claude
      model: claude-opus-4
""",
        encoding="utf-8",
    )
    worktree.write_text(
        """
build:
  clis:
    - cli: gemini
      model: gpt-gemini
""",
        encoding="utf-8",
    )

    result = load_phase_step_model(step_name="build", local_path=worktree, repo_path=repo)
    assert result.name == "RepoName"
    assert result.role == "developer"
    assert result.clis == (("gemini", "gpt-gemini"),)
    assert result.model == "gpt-gemini"


def test_phase_config_rejects_non_string_top_level_keys(tmp_path):
    config_path = tmp_path / "phases.yaml"
    config_path.write_text(
        """
123:
  clis:
    - cli: codex
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="top-level keys must be strings") as exc_info:
        load_phase_step_model(
            step_name="123",
            local_path=config_path,
            repo_path=None,
        )

    message = str(exc_info.value)
    assert config_path.as_posix() in message
    assert "step='unknown'" in message
    assert "field='step'" in message
