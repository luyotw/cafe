import os
import subprocess
from pathlib import Path


def _write_fake_git(bin_dir: Path) -> None:
    (bin_dir / "git").write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "$1" == "rev-parse" && "$2" == "--abbrev-ref" && "$3" == "HEAD" ]]; then
  echo "feature/test"
  exit 0
fi
if [[ "$1" == "status" && "$2" == "--porcelain" ]]; then
  if [[ "${FAKE_GIT_DIRTY:-}" == "1" ]]; then
    echo "M  src/app.py"
  fi
  exit 0
fi
if [[ "$1" == "push" ]]; then
  if [[ "${FAKE_GIT_PUSH_FAIL:-}" == "1" ]]; then
    echo "push failed" >&2
    exit 1
  fi
  exit 0
fi
echo "unsupported git args: $*" >&2
exit 1
""",
        encoding="utf-8",
    )
    (bin_dir / "git").chmod(0o755)


def _write_fake_gh(bin_dir: Path, log_file: Path) -> None:
    (bin_dir / "gh").write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
if [[ "$1" == "pr" && "$2" == "view" ]]; then
  if [[ "${{FAKE_GH_NO_PR:-}}" == "1" ]]; then
    exit 1
  fi
  echo '{{"number":77,"url":"https://github.com/demo/repo/pull/77","state":"OPEN","baseRefName":"dev"}}'
  exit 0
fi
if [[ "$1" == "pr" && "$2" == "edit" ]]; then
  echo "edit:$*" >> "{log_file}"
  exit 0
fi
if [[ "$1" == "pr" && "$2" == "comment" ]]; then
  echo "comment:$*" >> "{log_file}"
  exit 0
fi
if [[ "$1" == "pr" && "$2" == "create" ]]; then
  echo "create:$*" >> "{log_file}"
  echo "https://github.com/demo/repo/pull/77"
  exit 0
fi
echo "unsupported gh args: $*" >&2
exit 1
""",
        encoding="utf-8",
    )
    (bin_dir / "gh").chmod(0o755)


def _run_sync_pr(project_root: Path, output_file: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    script = project_root / "src/cafe/data/skills/pr/scripts/sync_pr.sh"
    return subprocess.run(
        ["/bin/bash", str(script), "--output", str(output_file), "--base", "main"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_sync_pr_posts_todo_comment_only_when_all_items_checked(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[2]
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    pr_iter = issue_dir / "pr" / "iteration_010"
    todo_iter = issue_dir / "pr" / "iteration_009"
    pr_iter.mkdir(parents=True, exist_ok=True)
    todo_iter.mkdir(parents=True, exist_ok=True)

    output_file = pr_iter / "output.md"
    output_file.write_text("# PR title\n\nBody content\n", encoding="utf-8")
    (todo_iter / "user_input.md").write_text("review comments", encoding="utf-8")
    (todo_iter / "output.md").write_text("## Todo List\n- [x] done\n", encoding="utf-8")
    (issue_dir / "issue.yaml").write_text("pr:\n  post_todo_list: true\n", encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_file = tmp_path / "gh.log"
    _write_fake_git(bin_dir)
    _write_fake_gh(bin_dir, log_file)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"

    result = _run_sync_pr(project_root, output_file, env)
    assert result.returncode == 0
    assert "comment:" in log_file.read_text(encoding="utf-8")


def test_sync_pr_skips_todo_comment_when_items_unchecked(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[2]
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    pr_iter = issue_dir / "pr" / "iteration_010"
    todo_iter = issue_dir / "pr" / "iteration_009"
    pr_iter.mkdir(parents=True, exist_ok=True)
    todo_iter.mkdir(parents=True, exist_ok=True)

    output_file = pr_iter / "output.md"
    output_file.write_text("# PR title\n\nBody content\n", encoding="utf-8")
    (todo_iter / "user_input.md").write_text("review comments", encoding="utf-8")
    (todo_iter / "output.md").write_text("## Todo List\n- [ ] pending\n", encoding="utf-8")
    (issue_dir / "issue.yaml").write_text("pr:\n  post_todo_list: true\n", encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_file = tmp_path / "gh.log"
    _write_fake_git(bin_dir)
    _write_fake_gh(bin_dir, log_file)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"

    result = _run_sync_pr(project_root, output_file, env)
    assert result.returncode == 0
    if log_file.exists():
        assert "comment:" not in log_file.read_text(encoding="utf-8")

def test_builtin_playbooks_publish_pr_through_sync_hook() -> None:
    project_root = Path(__file__).resolve().parents[2]
    for rel_path in [
        "src/cafe/data/playbooks/default.yaml",
        "src/cafe/data/playbooks/simple.yaml",
        "src/cafe/data/playbooks/hotfix.yaml",
    ]:
        content = (project_root / rel_path).read_text(encoding="utf-8")
        assert "publish_output: [GitHubPRCreator, LocalPRReviewer, PRLinkOpener]" in content
def test_sync_pr_rejects_uncommitted_changes(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[2]
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    pr_iter = issue_dir / "pr" / "iteration_010"
    pr_iter.mkdir(parents=True, exist_ok=True)
    output_file = pr_iter / "output.md"
    output_file.write_text("# PR title\n\nBody content\n", encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_file = tmp_path / "gh.log"
    _write_fake_git(bin_dir)
    _write_fake_gh(bin_dir, log_file)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["FAKE_GIT_DIRTY"] = "1"

    result = _run_sync_pr(project_root, output_file, env)

    assert result.returncode == 1
    assert "cannot sync PR with uncommitted changes" in result.stderr
    assert not log_file.exists()


def test_sync_pr_fails_when_branch_push_fails(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[2]
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    pr_iter = issue_dir / "pr" / "iteration_010"
    pr_iter.mkdir(parents=True, exist_ok=True)
    output_file = pr_iter / "output.md"
    output_file.write_text("# PR title\n\nBody content\n", encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_file = tmp_path / "gh.log"
    _write_fake_git(bin_dir)
    _write_fake_gh(bin_dir, log_file)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["FAKE_GIT_PUSH_FAIL"] = "1"

    result = _run_sync_pr(project_root, output_file, env)

    assert result.returncode == 1
    assert "failed to push branch" in result.stderr
    assert not log_file.exists()


def test_sync_pr_uses_pushed_head_when_creating_pr(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[2]
    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    pr_iter = issue_dir / "pr" / "iteration_010"
    pr_iter.mkdir(parents=True, exist_ok=True)
    output_file = pr_iter / "output.md"
    output_file.write_text("# PR title\n\nBody content\n", encoding="utf-8")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_file = tmp_path / "gh.log"
    _write_fake_git(bin_dir)
    _write_fake_gh(bin_dir, log_file)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["FAKE_GH_NO_PR"] = "1"

    result = _run_sync_pr(project_root, output_file, env)

    assert result.returncode == 0
    assert "--head feature/test" in log_file.read_text(encoding="utf-8")
