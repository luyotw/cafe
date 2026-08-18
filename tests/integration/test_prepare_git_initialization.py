"""End-to-end coverage for guided Git initialization during prepare."""

from unittest.mock import patch

from typer.testing import CliRunner

from cafe.core.git import GitError, GitOperations
from cafe.ui.cli import app
from tests.conftest import create_minimal_config


def test_prepare_initializes_git_only_with_explicit_non_interactive_flag(
    tmp_path, monkeypatch
) -> None:
    create_minimal_config(tmp_path)
    (tmp_path / "private-notes.txt").write_text("not staged", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")

    result = CliRunner().invoke(
        app,
        [
            "prepare",
            "first-task",
            "--no-interactive",
            "--init-git",
            "--input-method=manual",
            "--rigor=medium",
            "--spec-template=auto",
            "--plan-template=default",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "CAFE and/or cafe@local.invalid" in result.output
    git = GitOperations(tmp_path)
    assert git.get_current_branch() == "first-task"
    assert git.run_git("log", "main", "-1", "--pretty=%s") == "Initialize repository"
    assert "?? private-notes.txt" in git.get_status()


def test_prepare_interactive_decline_leaves_folder_without_git(tmp_path, monkeypatch) -> None:
    create_minimal_config(tmp_path)
    monkeypatch.chdir(tmp_path)

    with patch("cafe.ui.cli.prompt_confirm", return_value=False):
        result = CliRunner().invoke(app, ["prepare", "first-task"])

    assert result.exit_code == 1
    assert "Git was not initialized" in result.output
    assert not (tmp_path / ".git").exists()


def test_prepare_resumes_baseline_after_commit_failure(tmp_path, monkeypatch) -> None:
    create_minimal_config(tmp_path)
    (tmp_path / "app.py").write_text("print('ready')\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    original_run_git = GitOperations.run_git
    fail_baseline_once = True

    def run_git_with_one_failure(self, *args):
        nonlocal fail_baseline_once
        if fail_baseline_once and "commit" in args:
            fail_baseline_once = False
            raise GitError("simulated baseline failure")
        return original_run_git(self, *args)

    first_arguments = [
        "prepare",
        "first-task",
        "--no-interactive",
        "--init-git",
        "--input-method=manual",
        "--rigor=medium",
        "--spec-template=auto",
        "--plan-template=default",
    ]
    retry_arguments = [argument for argument in first_arguments if argument != "--init-git"]

    with patch.object(GitOperations, "run_git", new=run_git_with_one_failure):
        first_result = CliRunner().invoke(app, first_arguments)
        retry_result = CliRunner().invoke(app, retry_arguments)

    assert first_result.exit_code == 1
    assert retry_result.exit_code == 0, retry_result.output
    git = GitOperations(tmp_path)
    assert git.run_git("log", "main", "-1", "--pretty=%s") == "Initialize repository"
    assert git.get_current_branch() == "first-task"


def test_prepare_keeps_worktree_blocked_when_initialization_is_retried(
    tmp_path, monkeypatch
) -> None:
    create_minimal_config(tmp_path)
    (tmp_path / "private-notes.txt").write_text("not staged", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    runner = CliRunner()
    common_arguments = [
        "prepare",
        "first-task",
        "--no-interactive",
        "--worktree",
        ".cafe/worktrees/first-task",
        "--input-method=manual",
        "--rigor=medium",
        "--spec-template=auto",
        "--plan-template=default",
    ]

    first_result = runner.invoke(app, [*common_arguments, "--init-git"])
    second_result = runner.invoke(app, common_arguments)

    assert first_result.exit_code == 1
    assert second_result.exit_code == 1
    assert "initial project files" in first_result.output
    assert "initial project files" in second_result.output
    assert not (tmp_path / ".cafe" / "worktrees" / "first-task").exists()


def test_pending_bootstrap_still_warns_about_tracked_modifications(tmp_path, monkeypatch) -> None:
    create_minimal_config(tmp_path)
    tracked_file = tmp_path / "app.py"
    tracked_file.write_text("print('initial')\n", encoding="utf-8")
    (tmp_path / "still-untracked.txt").write_text("pending", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    git = GitOperations.initialize_repository(str(tmp_path))
    git.run_git("add", "app.py")
    git.run_git("commit", "--no-gpg-sign", "-m", "Track application")
    tracked_file.write_text("print('modified')\n", encoding="utf-8")

    with patch("cafe.ui.cli.prompt_confirm", return_value=False):
        result = CliRunner().invoke(app, ["prepare", "second-task"])

    assert result.exit_code == 0
    assert "Warning: You have uncommitted changes" in result.output
    assert "Cancelled" in result.output
    assert git.get_current_branch() == "main"
    assert not git.branch_exists("second-task")


def test_pending_bootstrap_blocks_worktree_for_staged_starting_file(tmp_path, monkeypatch) -> None:
    create_minimal_config(tmp_path)
    staged_file = tmp_path / "app.py"
    staged_file.write_text("print('staged')\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    git = GitOperations.initialize_repository(str(tmp_path))
    git.run_git("add", "app.py")

    result = CliRunner().invoke(
        app,
        [
            "prepare",
            "first-task",
            "--no-check",
            "--worktree",
            ".cafe/worktrees/first-task",
        ],
    )

    assert result.exit_code == 1
    assert "initial project files" in result.output
    assert git.get_current_branch() == "main"
    assert not git.branch_exists("first-task")
