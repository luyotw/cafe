"""Publishing must validate its Python before any remote mutation."""

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from cafe.core import capabilities as cap

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = {
    "pr": "cafe-pr/scripts/sync_pr.sh",
    "plan": "cafe-github_sync/scripts/sync_github.sh",
}


def executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def publish_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    project = tmp_path / "project"
    project.mkdir()
    bin_dir = tmp_path / "bin"
    remote_log = tmp_path / "remote.log"
    python_log = tmp_path / "python.log"
    # A real Python without site-packages reproduces the missing PyYAML environment.
    bad_python = tmp_path / "without yaml" / "python"
    executable(
        bad_python,
        f"#!/bin/bash\nprintf 'used\\n' >> {shlex.quote(str(python_log))}\n"
        f'exec {shlex.quote(sys.executable)} -S "$@"\n',
    )
    executable(
        bin_dir / "git",
        """#!/bin/bash
if [[ "$1" == rev-parse && "$2" == --show-toplevel ]]; then
  echo "$TEST_PROJECT"
elif [[ "$1" == rev-parse ]]; then
  echo issue1
elif [[ "$1" == status ]]; then
  exit 0
elif [[ "$1" == push ]]; then
  echo push >> "$REMOTE_LOG"
else
  exit 1
fi
""",
    )
    executable(
        bin_dir / "gh",
        """#!/bin/bash
if [[ -n "${TEST_TOKEN:-}" && "${GH_TOKEN:-}" != "$TEST_TOKEN" ]]; then
  echo 'Missing inherited GitHub authentication' >&2
  exit 1
fi
if [[ "$1 $2" == 'pr view' ]]; then
  if [[ "${TEST_EXISTING_PR:-0}" == 1 ]]; then
    echo '{"number":42,"url":"https://github.com/example/repo/pull/42","state":"OPEN","baseRefName":"main"}'
  else
    exit 1
  fi
elif [[ "$1 $2" == 'auth status' ]]; then
  exit 0
else
  echo "$1 $2" >> "$REMOTE_LOG"
  if [[ "$1 $2" == 'pr create' ]]; then
    echo https://github.com/example/repo/pull/42
  fi
fi
""",
    )
    (bin_dir / "python3").symlink_to(bad_python)
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.setenv("TEST_PROJECT", str(project))
    monkeypatch.setenv("REMOTE_LOG", str(remote_log))
    monkeypatch.delenv("CAFE_PYTHON", raising=False)
    return project, bin_dir, bad_python, remote_log, python_log


def prepare_script(tmp_path: Path, phase: str) -> Path:
    # Packaged scripts cannot rely on the CAFE source checkout's .venv.
    path = tmp_path / "installed/lib/cafe/data/skills" / SCRIPTS[phase]
    path.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "src/cafe/data/skills" / SCRIPTS[phase], path)
    return path


def output_file(project: Path, phase: str) -> Path:
    issue = project / ".cafe/issues/issue1"
    output = issue / phase / "iteration_001/output.md"
    output.parent.mkdir(parents=True)
    output.write_text("# PR title\n\n## Todo List\n- [x] done\n", encoding="utf-8")
    output.with_name("user_input.md").write_text("review", encoding="utf-8")
    (issue / "issue.yaml").write_text(
        "spec:\n  issue_id: 1\nplan:\n  sync_github: true\npr:\n  post_todo_list: true\n",
        encoding="utf-8",
    )
    return output


def run_script(script: Path, output: Path, phase: str, project: Path):
    args = ["/bin/bash", str(script), "--output", str(output)]
    if phase != "pr":
        args += ["--phase", phase]
    return subprocess.run(args, cwd=project, text=True, capture_output=True, check=False)


@pytest.mark.parametrize("existing_pr", [False, True])
def test_host_publish_uses_its_python_instead_of_project_or_path(
    publish_environment, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing_pr: bool
):
    project, _, bad_python, log, python_log = publish_environment
    local_python = project / ".venv/bin/python"
    local_python.parent.mkdir(parents=True)
    local_python.symlink_to(bad_python)
    monkeypatch.setenv("CAFE_PYTHON", str(bad_python))
    monkeypatch.setenv("TEST_EXISTING_PR", str(int(existing_pr)))
    monkeypatch.setenv("GH_TOKEN", "test-token-preserved")
    monkeypatch.setenv("TEST_TOKEN", "test-token-preserved")
    script = prepare_script(tmp_path, "pr")
    monkeypatch.setattr(cap, "resolve_sync_pr_script", lambda _root: script)
    output = output_file(project, "pr")
    definition = {
        "script_ref": "sync_pr",
        "args_schema": {"required": ["output"]},
        "expected_outputs": {"required": ["pr_url", "pr_number"]},
    }
    result = cap.run_pr_publish_capability(
        repo_root=project,
        registry={cap.CAPABILITY_PR_PUBLISH_ID: definition},
        publish_request={
            "capability": cap.CAPABILITY_PR_PUBLISH_ID,
            "args": {"output": str(output)},
        },
        pr_markdown_file=output,
    )
    assert result.receipt["success"] is True, result.receipt
    assert result.receipt["outputs"]["pr_url"].endswith("/42")
    assert log.read_text().splitlines() == [
        "push",
        "pr edit" if existing_pr else "pr create",
        "pr comment",
    ]
    assert not python_log.exists()
    assert os.environ["CAFE_PYTHON"] == str(bad_python)
    assert os.environ["GH_TOKEN"] == "test-token-preserved"


@pytest.mark.parametrize("phase", SCRIPTS)
def test_explicit_python_with_spaces_wins_over_missing_yaml_on_path(
    publish_environment, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
):
    project, _, _, log, python_log = publish_environment
    valid = tmp_path / "runtime with spaces/python"
    valid.parent.mkdir(parents=True)
    valid.symlink_to(sys.executable)
    monkeypatch.setenv("CAFE_PYTHON", str(valid))
    result = run_script(
        prepare_script(tmp_path, phase), output_file(project, phase), phase, project
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["action"] in {"created", "commented"}
    assert log.exists()
    assert not python_log.exists()


@pytest.mark.parametrize("phase", SCRIPTS)
@pytest.mark.parametrize("explicit", [False, True])
def test_missing_yaml_fails_before_push_or_github_mutation(
    publish_environment, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str, explicit: bool
):
    project, _, bad_python, log, _ = publish_environment
    if explicit:
        monkeypatch.setenv("CAFE_PYTHON", str(bad_python))
    result = run_script(
        prepare_script(tmp_path, phase), output_file(project, phase), phase, project
    )
    assert result.returncode != 0
    assert "PyYAML" in result.stderr
    assert not log.exists()


@pytest.mark.parametrize("phase", SCRIPTS)
def test_standalone_skips_project_python_without_yaml(
    publish_environment, tmp_path: Path, phase: str
):
    project, bin_dir, bad_python, log, python_log = publish_environment
    local_python = project / ".venv/bin/python"
    local_python.parent.mkdir(parents=True)
    local_python.symlink_to(bad_python)
    (bin_dir / "python3").unlink()
    (bin_dir / "python3").symlink_to(sys.executable)
    result = run_script(
        prepare_script(tmp_path, phase), output_file(project, phase), phase, project
    )
    assert result.returncode == 0, result.stderr
    assert python_log.exists()  # Rejected candidate was probed, not used for publishing.
    assert log.exists()


@pytest.mark.parametrize("phase", SCRIPTS)
@pytest.mark.parametrize("optimized", [False, True])
def test_unsupported_python_fails_before_remote_mutation(
    publish_environment,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    optimized: bool,
):
    project, _, _, log, _ = publish_environment
    if optimized:
        monkeypatch.setenv("PYTHONOPTIMIZE", "1")
    else:
        monkeypatch.delenv("PYTHONOPTIMIZE", raising=False)
    old = tmp_path / "old-python"
    executable(
        old,
        f"#!/bin/bash\nexec {shlex.quote(sys.executable)} -c "
        "'import sys; sys.version_info=(3, 7); exec(sys.argv[1])' \"$2\"\n",
    )
    monkeypatch.setenv("CAFE_PYTHON", str(old))
    result = run_script(
        prepare_script(tmp_path, phase), output_file(project, phase), phase, project
    )
    assert result.returncode != 0
    assert not log.exists()
