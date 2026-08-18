from pathlib import Path

import pytest

from cafe.utils.phase_config import load_phase_step_model


def test_load_phase_step_model_reports_step_field_for_invalid_clis(tmp_path: Path) -> None:
    invalid_yaml = """
spec:
  clis:
    - cli: codex
      model: gpt
    - cli: codex
      model: sonnet
"""
    config_path = tmp_path / "phases.yaml"
    config_path.write_text(invalid_yaml, encoding="utf-8")

    with pytest.raises(ValueError, match=r"field=['\"]spec\.clis"):
        load_phase_step_model(step_name="spec", local_path=config_path)
