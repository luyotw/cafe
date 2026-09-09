import os
import stat
import subprocess
from pathlib import Path


SCRIPT_PATH = Path("src/cafe/data/skills/cafe-pr/scripts/sync_pr.sh")


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _setup_fake_bin(tmp_path: Path, gh_body: str) -> Path:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()

    _write_executable(
        fake_bin / "git",
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "$1" == "rev-parse" ]]; then
  echo "issue231"
  exit 0
fi
if [[ "$1" == "status" && "$2" == "--porcelain" ]]; then
  exit 0
fi
if [[ "$1" == "fetch" ]]; then
  exit 0
fi
if [[ "$1" == "merge-base" && "$2" == "--is-ancestor" ]]; then
  exit 0
fi
if [[ "$1" == "push" ]]; then
  exit 0
fi
exit 1
""",
    )

    _write_executable(
        fake_bin / "gh",
        f"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$GH_LOG"
if [[ "$1" == "pr" && "$2" == "view" ]]; then
  cat <<'EOF'
{gh_body}
EOF
  exit 0
fi
if [[ "$1" == "pr" && "$2" == "edit" ]]; then
  exit 0
fi
if [[ "$1" == "pr" && "$2" == "create" ]]; then
  echo "https://github.com/example/repo/pull/999"
  exit 0
fi
exit 1
""",
    )

    return fake_bin


def _write_output_file(tmp_path: Path) -> Path:
    output_file = tmp_path / "output.md"
    output_file.write_text("# Test PR Title\n\nPR body\n")
    return output_file


def test_sync_pr_updates_open_pr_and_retargets_base(tmp_path: Path) -> None:
    fake_bin = _setup_fake_bin(
        tmp_path,
        '{"number":236,"url":"https://github.com/example/repo/pull/236","state":"OPEN","baseRefName":"main"}',
    )
    output_file = _write_output_file(tmp_path)
    gh_log = tmp_path / "gh.log"

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["GH_LOG"] = str(gh_log)

    result = subprocess.run(
        ["bash", str(SCRIPT_PATH), "--output", str(output_file), "--base", "v02"],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert '"action":"updated"' in result.stdout
    log_lines = gh_log.read_text().splitlines()
    assert "pr view --json number,url,state,baseRefName" in log_lines[0]
    assert any("pr edit 236 --title Test PR Title --body PR body" in line for line in log_lines)
    assert any("pr edit 236 --base v02" in line for line in log_lines)


def test_sync_pr_creates_new_pr_when_existing_pr_is_closed(tmp_path: Path) -> None:
    fake_bin = _setup_fake_bin(
        tmp_path,
        '{"number":236,"url":"https://github.com/example/repo/pull/236","state":"CLOSED","baseRefName":"main"}',
    )
    output_file = _write_output_file(tmp_path)
    gh_log = tmp_path / "gh.log"

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["GH_LOG"] = str(gh_log)

    result = subprocess.run(
        ["bash", str(SCRIPT_PATH), "--output", str(output_file), "--base", "v02"],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert '"action":"created"' in result.stdout
    log_lines = gh_log.read_text().splitlines()
    assert "pr view --json number,url,state,baseRefName" in log_lines[0]
    assert any("pr create --title Test PR Title --body PR body --head issue231 --base v02" in line for line in log_lines)
    assert not any("pr edit 236 --title Test PR Title --body PR body" in line for line in log_lines)
