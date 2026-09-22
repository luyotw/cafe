"""Tests for read-only CI/CD inference used by the Driver kickoff contract."""

import importlib.util
import re
from pathlib import Path


SCRIPT = (
    Path(__file__).parents[2]
    / "src/cafe/data/skills/use-cafe-workflow/scripts/infer_closeout_scope.py"
)
spec = importlib.util.spec_from_file_location("infer_closeout_scope", SCRIPT)
inference = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(inference)


def test_github_actions_inference_suggests_pr_delivery_without_authorizing_it(
    tmp_path: Path,
) -> None:
    workflow = tmp_path / ".github" / "workflows" / "ci.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        """
name: CI
on:
  pull_request:
  push:
jobs:
  verify:
    runs-on: ubuntu-latest
  deploy-preview:
    steps:
      - run: publish preview
""",
        encoding="utf-8",
    )

    result = inference.infer_closeout_scope(tmp_path)

    assert result["configurations"] == [
        {
            "path": ".github/workflows/ci.yml",
            "system": "github_actions",
            "signals": ["pull_request", "push", "deploy", "publish"],
        }
    ]
    assert result["suggested_deliver_scope"] == [
        "publish_pull_request",
        "wait_for_ci_checks",
        "merge_pull_request",
    ]
    assert result["suggested_cleanup_scope"] == [
        "archive_cafe_issue_after_integration",
        "remove_feature_worktree",
        "delete_feature_branch",
    ]
    assert result["requires_explicit_confirmation"] == [
        "merge_pull_request",
        "deploy",
        "publish",
    ]


def test_empty_repository_gets_a_deterministic_manual_handoff_proposal(tmp_path: Path) -> None:
    first = inference.infer_closeout_scope(tmp_path)
    second = inference.infer_closeout_scope(tmp_path)

    assert first == second
    assert first["configurations"] == []
    assert first["suggested_deliver_scope"] == ["manual_delivery_handoff"]
    assert first["requires_explicit_confirmation"] == ["merge_pull_request"]
    assert re.fullmatch(r"[0-9a-f]{64}", first["fingerprint_sha256"])


def test_ci_cd_fingerprint_changes_when_recognized_configuration_changes(tmp_path: Path) -> None:
    config = tmp_path / ".gitlab-ci.yml"
    config.write_text("test:\n  script: pytest\n", encoding="utf-8")
    first = inference.infer_closeout_scope(tmp_path)
    config.write_text("test:\n  script: pytest -q\n", encoding="utf-8")
    second = inference.infer_closeout_scope(tmp_path)

    assert first["fingerprint_sha256"] != second["fingerprint_sha256"]
