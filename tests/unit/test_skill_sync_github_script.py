import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml


def test_phase_scripts_delegate_to_shared_implementation() -> None:
    project_root = Path(__file__).resolve().parents[2]
    shared = project_root / "src/cafe/data/skills/cafe-github_sync/scripts/sync_github.sh"
    assert shared.exists()

    wrappers = [
        project_root / "src/cafe/data/skills/cafe-spec/scripts/sync_github.sh",
        project_root / "src/cafe/data/skills/cafe-plan/scripts/sync_github.sh",
    ]

    for wrapper in wrappers:
        content = wrapper.read_text(encoding="utf-8")
        assert "../../cafe-github_sync/scripts/sync_github.sh" in content
        assert "exec /bin/bash" in content


@pytest.mark.parametrize(
    "script_rel_path,phase",
    [
        ("src/cafe/data/skills/cafe-spec/scripts/sync_github.sh", "spec"),
        ("src/cafe/data/skills/cafe-plan/scripts/sync_github.sh", "plan"),
    ],
)
def test_sync_script_skips_when_sync_disabled_without_gh(
    tmp_path: Path, script_rel_path: str, phase: str
) -> None:
    project_root = Path(__file__).resolve().parents[2]
    script_path = project_root / script_rel_path

    issue_dir = tmp_path / ".cafe" / "issues" / "demo"
    output_file = issue_dir / phase / "iteration_001" / "output.md"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text("# Output\n\ncontent", encoding="utf-8")

    issue_yaml = issue_dir / "issue.yaml"
    issue_yaml.write_text(
        f"spec:\n  issue_id: 123\n{phase}:\n  sync_github: false\n",
        encoding="utf-8",
    )

    python_path = subprocess.check_output(["which", "python3"], text=True).strip()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python3").symlink_to(python_path)

    env = os.environ.copy()
    env["PATH"] = str(bin_dir)

    result = subprocess.run(
        ["/bin/bash", str(script_path), "--phase", phase, "--output", str(output_file)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout.strip())
    assert payload["action"] == "skipped"
    assert payload["reason"] == "sync_disabled"


def test_standard_playbooks_leave_confirmed_sync_to_trusted_runtime() -> None:
    project_root = Path(__file__).resolve().parents[2]
    for playbook_name in ("standard", "standard-qa", "tdd", "tdd-qa"):
        playbook_path = project_root / f"src/cafe/data/playbooks/{playbook_name}.yaml"
        data = yaml.safe_load(playbook_path.read_text(encoding="utf-8"))
        for phase in ("spec", "plan"):
            hooks = data["steps"][phase]["hooks"]
            assert not any(
                isinstance(entry, dict) and "capability" in entry
                for entries in hooks.values()
                for entry in entries
            )


def test_standard_playbook_no_changes_needed_routes_to_review() -> None:
    project_root = Path(__file__).resolve().parents[2]
    playbook_path = project_root / "src/cafe/data/playbooks/standard.yaml"
    data = yaml.safe_load(playbook_path.read_text(encoding="utf-8"))

    develop_on = data["steps"]["develop"]["on"]
    assert develop_on["no_changes_needed"] == "review"
