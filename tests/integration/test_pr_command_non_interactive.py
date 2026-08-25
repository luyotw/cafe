"""Integration tests for PR publish path (utilities + sync_pr.sh, no PRPhase)."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from cafe.core.blackboard import HandoffIntent, HandoffOwner
from cafe.core.workflow_models import StepExecutionResult
from cafe.ui.cli import app
from cafe.utils.pr import parse_pr_body, parse_pr_title

SCRIPT_PATH = Path("src/cafe/data/skills/cafe-pr/scripts/sync_pr.sh")
runner = CliRunner()


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _setup_fake_bin(tmp_path: Path, *, create_body: str) -> Path:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    gh_log = tmp_path / "gh.log"
    _write_executable(
        fake_bin / "git",
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "$1" == "rev-parse" ]]; then echo "test-issue"; exit 0; fi
if [[ "$1" == "status" && "$2" == "--porcelain" ]]; then exit 0; fi
if [[ "$1" == "fetch" ]]; then exit 0; fi
if [[ "$1" == "merge-base" && "$2" == "--is-ancestor" ]]; then exit 0; fi
if [[ "$1" == "push" ]]; then exit 0; fi
exit 1
""",
    )
    _write_executable(
        fake_bin / "gh",
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "{gh_log}"
if [[ "$1" == "pr" && "$2" == "view" ]]; then
  exit 1
fi
if [[ "$1" == "pr" && "$2" == "create" ]]; then
  echo "https://github.com/example/repo/pull/99"
  exit 0
fi
exit 1
""",
    )
    return fake_bin


def test_parse_pr_title_and_body_from_output_md(tmp_path: Path) -> None:
    output = tmp_path / "output.md"
    output.write_text(
        "# Custom PR Title\n\n## Summary\nLine one\n\n- item\n",
        encoding="utf-8",
    )
    content = output.read_text(encoding="utf-8")
    assert parse_pr_title(content) == "Custom PR Title"
    assert "Line one" in parse_pr_body(content)
    assert "- item" in parse_pr_body(content)


def test_sync_pr_create_uses_parsed_title_and_body(tmp_path: Path) -> None:
    fake_bin = _setup_fake_bin(tmp_path, create_body="")
    output_file = tmp_path / "output.md"
    output_file.write_text("# Draft Feature\n\n## Summary\nShip it\n", encoding="utf-8")
    gh_log = tmp_path / "gh.log"
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

    result = subprocess.run(
        ["bash", str(SCRIPT_PATH), "--output", str(output_file), "--base", "main"],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert '"action":"created"' in result.stdout
    log_lines = gh_log.read_text().splitlines()
    assert any("pr create --title Draft Feature" in line for line in log_lines)
    assert any("pr create" in line and "Draft Feature" in line for line in log_lines)


def test_cafe_workflow_pr_non_interactive_routes_through_runtime(tmp_path: Path, monkeypatch) -> None:
    """Non-interactive PR step runs via cafe workflow --start-step pr --execute."""
    monkeypatch.chdir(tmp_path)
    from tests.conftest import create_minimal_config

    create_minimal_config(tmp_path)
    issue_name = "test-issue"
    issue_dir = tmp_path / ".cafe" / "issues" / issue_name
    spec_dir = issue_dir / "spec" / "iteration_001"
    plan_dir = issue_dir / "plan" / "iteration_001"
    spec_dir.mkdir(parents=True)
    plan_dir.mkdir(parents=True)
    (spec_dir / "output.md").write_text("# Spec\n", encoding="utf-8")
    (plan_dir / "output.md").write_text("# Plan\n", encoding="utf-8")
    (issue_dir / "blackboard.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "playbook_id": "standard",
                "current_step": "pr",
                "artifacts": {},
                "events": [],
                "decisions": [],
            }
        ),
        encoding="utf-8",
    )

    pr_output = issue_dir / "pr" / "iteration_001" / "output.md"
    pr_output.parent.mkdir(parents=True, exist_ok=True)

    class FakeExecutor:
        def execute_step(self, step_name: str, step_def: dict, blackboard_state: object, **kwargs) -> StepExecutionResult:
            assert step_name == "pr"
            pr_output.write_text("# PR\n\n## Summary\nDone\n", encoding="utf-8")
            return StepExecutionResult(
                response="confirmed",
                artifacts={"pr": str(pr_output)},
                status_code="confirmed",
                handoff_owner=HandoffOwner.DONE,
                handoff_intent=HandoffIntent.WORKFLOW_COMPLETE,
            )

    with patch("cafe.ui.cli.GitOperations") as mock_git_cls, patch(
        "cafe.ui.cli._build_workflow_step_executor", return_value=FakeExecutor()
    ):
        git = MagicMock()
        git.is_valid_branch.return_value = True
        git.get_current_branch.return_value = issue_name
        mock_git_cls.return_value = git
        result = runner.invoke(
            app,
            ["workflow", "--start-step", "pr", "--execute", "--single-step"],
            catch_exceptions=False,
        )

    assert result.exit_code == 0
    assert "Executing step=pr" in result.stdout
