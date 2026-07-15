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
""",
        encoding="utf-8",
    )

    result = load_phase_step_model(step_name="build", local_path=config)

    assert result.name == "Build step"
    assert result.role == "developer"
    assert result.clis == (("claude", "gpt-5"), ("gemini", None))
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


def test_model_from_cli_clis_prefers_first_model_entry():
    assert model_from_cli_clis((("claude", "opus"), ("gemini", "sonnet"))) == "opus"


def test_load_phase_step_model_accepts_empty_phase_entry_as_fallback_only(tmp_path):
    config = tmp_path / "phases.yaml"
    config.write_text("build: {}\n", encoding="utf-8")

    result = load_phase_step_model(step_name="build", local_path=config)

    assert result.name is None
    assert result.role is None
    assert result.clis == ()
    assert result.model is None


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

    with pytest.raises(ValueError, match="top-level keys must be strings"):
        load_phase_step_model(
            step_name="123",
            local_path=config_path,
            repo_path=None,
        )
