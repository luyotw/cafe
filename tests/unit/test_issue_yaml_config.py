"""Phase-agnostic tests for issue.yaml spec.sync_github and plan.sync_github configuration."""

from pathlib import Path

import pytest
import yaml

from cafe.core.types import SpecRigor
from cafe.utils.config import resolve_sync_github_config


def _read_issue_config(config_file: Path) -> dict:
    if not config_file.exists():
        return {}
    return yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}


def _load_spec_sync_github(issue_dir: Path, cli_value: bool | None = None) -> bool:
    """Resolve spec.sync_github the same way the workflow runtime does (issue.yaml)."""
    config_data = _read_issue_config(issue_dir / "issue.yaml")
    spec_config = config_data.get("spec", {}) if config_data else {}
    config_value = bool(spec_config["sync_github"]) if "sync_github" in spec_config else None
    has_issue_id = bool(spec_config.get("issue_id"))
    return resolve_sync_github_config(
        cli_value=cli_value,
        config_value=config_value,
        has_issue_id=has_issue_id,
    )


def _save_spec_issue_config(
    issue_dir: Path,
    *,
    issue_id: int | None,
    rigor: SpecRigor,
    sync_github: bool,
) -> None:
    """Persist spec.sync_github in issue.yaml while preserving unrelated keys."""
    config_file = issue_dir / "issue.yaml"
    existing = _read_issue_config(config_file)
    config_data = {**existing}
    config_data.setdefault("spec", {})
    if issue_id:
        config_data["spec"]["issue_id"] = issue_id
        config_data["spec"]["input_method"] = "github"
    config_data["spec"]["rigor"] = rigor.value
    config_data["spec"]["sync_github"] = sync_github
    config_file.write_text(yaml.dump(config_data, default_flow_style=False), encoding="utf-8")


class TestLoadSpecSyncGithub:
    def test_load_sync_github_true_from_config(self, tmp_path: Path) -> None:
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        issue_dir.mkdir(parents=True)
        (issue_dir / "issue.yaml").write_text(
            "spec:\n  issue_id: 123\n  sync_github: true\n",
            encoding="utf-8",
        )
        assert _load_spec_sync_github(issue_dir) is True

    def test_load_sync_github_false_from_config(self, tmp_path: Path) -> None:
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        issue_dir.mkdir(parents=True)
        (issue_dir / "issue.yaml").write_text(
            "spec:\n  issue_id: 123\n  sync_github: false\n",
            encoding="utf-8",
        )
        assert _load_spec_sync_github(issue_dir) is False

    def test_default_sync_github_true_when_issue_id_present(self, tmp_path: Path) -> None:
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        issue_dir.mkdir(parents=True)
        (issue_dir / "issue.yaml").write_text(
            "spec:\n  issue_id: 123\n  rigor: medium\n",
            encoding="utf-8",
        )
        assert _load_spec_sync_github(issue_dir) is True

    def test_default_sync_github_false_when_no_issue_id(self, tmp_path: Path) -> None:
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        issue_dir.mkdir(parents=True)
        (issue_dir / "issue.yaml").write_text("spec:\n  rigor: medium\n", encoding="utf-8")
        assert _load_spec_sync_github(issue_dir) is False

    def test_no_config_file_defaults_to_false(self, tmp_path: Path) -> None:
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        issue_dir.mkdir(parents=True)
        assert _load_spec_sync_github(issue_dir) is False


class TestSaveSpecSyncGithub:
    def test_save_sync_github_true(self, tmp_path: Path) -> None:
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        issue_dir.mkdir(parents=True)
        _save_spec_issue_config(
            issue_dir, issue_id=123, rigor=SpecRigor.MEDIUM, sync_github=True
        )
        config = yaml.safe_load((issue_dir / "issue.yaml").read_text(encoding="utf-8"))
        assert config["spec"]["sync_github"] is True
        assert config["spec"]["issue_id"] == 123

    def test_save_sync_github_false(self, tmp_path: Path) -> None:
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        issue_dir.mkdir(parents=True)
        _save_spec_issue_config(
            issue_dir, issue_id=123, rigor=SpecRigor.MEDIUM, sync_github=False
        )
        config = yaml.safe_load((issue_dir / "issue.yaml").read_text(encoding="utf-8"))
        assert config["spec"]["sync_github"] is False

    def test_preserve_existing_config_when_saving(self, tmp_path: Path) -> None:
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        issue_dir.mkdir(parents=True)
        (issue_dir / "issue.yaml").write_text(
            "base_branch: main\nfeature_branch: test-issue\nspec:\n  rigor: high\n",
            encoding="utf-8",
        )
        _save_spec_issue_config(
            issue_dir, issue_id=123, rigor=SpecRigor.MEDIUM, sync_github=True
        )
        config = yaml.safe_load((issue_dir / "issue.yaml").read_text(encoding="utf-8"))
        assert config["base_branch"] == "main"
        assert config["feature_branch"] == "test-issue"
        assert config["spec"]["sync_github"] is True
        assert config["spec"]["issue_id"] == 123


def _load_plan_sync_github(issue_dir: Path, cli_value: bool | None = None) -> bool:
    """Resolve plan.sync_github the same way the workflow runtime does (issue.yaml)."""
    config_data = _read_issue_config(issue_dir / "issue.yaml")
    plan_config = config_data.get("plan", {}) if config_data else {}
    spec_config = config_data.get("spec", {}) if config_data else {}
    config_value = bool(plan_config["sync_github"]) if "sync_github" in plan_config else None
    has_issue_id = bool(spec_config.get("issue_id"))
    return resolve_sync_github_config(
        cli_value=cli_value,
        config_value=config_value,
        has_issue_id=has_issue_id,
    )


class TestLoadPlanSyncGithub:
    def test_load_sync_github_true_from_config(self, tmp_path: Path) -> None:
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        issue_dir.mkdir(parents=True)
        (issue_dir / "issue.yaml").write_text(
            "plan:\n  template: auto\n  sync_github: true\n",
            encoding="utf-8",
        )
        assert _load_plan_sync_github(issue_dir) is True

    def test_load_sync_github_false_from_config(self, tmp_path: Path) -> None:
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        issue_dir.mkdir(parents=True)
        (issue_dir / "issue.yaml").write_text(
            "plan:\n  template: auto\n  sync_github: false\n",
            encoding="utf-8",
        )
        assert _load_plan_sync_github(issue_dir) is False

    def test_default_sync_github_true_when_issue_id_in_spec_config(self, tmp_path: Path) -> None:
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        issue_dir.mkdir(parents=True)
        (issue_dir / "issue.yaml").write_text(
            "spec:\n  issue_id: 123\nplan:\n  template: auto\n",
            encoding="utf-8",
        )
        assert _load_plan_sync_github(issue_dir) is True

    def test_default_sync_github_false_when_no_issue_id(self, tmp_path: Path) -> None:
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        issue_dir.mkdir(parents=True)
        (issue_dir / "issue.yaml").write_text("plan:\n  template: auto\n", encoding="utf-8")
        assert _load_plan_sync_github(issue_dir) is False

    def test_no_config_file_defaults_to_false(self, tmp_path: Path) -> None:
        issue_dir = tmp_path / ".cafe" / "issues" / "test-issue"
        issue_dir.mkdir(parents=True)
        assert _load_plan_sync_github(issue_dir) is False
