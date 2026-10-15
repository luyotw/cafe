"""I1/I2/I3/I5/I6: public configured consumers and CI share executable authority."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from typer.testing import CliRunner

from cafe.ui.cli import app

RUNNER = CliRunner()
SOURCE = str(Path(__file__).resolve().parents[2] / "src")


def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def project(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    (repo / ".cafe").mkdir()
    (repo / ".gitignore").write_text(".cafe/\n__pycache__/\n.pytest_cache/\n")
    (repo / ".cafe/config.yaml").write_text("verification:\n  profile_file: profiles.json\n  profile: custom-consumer\n")
    (repo / "suite.py").write_text(
        "import os,sys\nfrom pathlib import Path\n"
        "with Path('.cafe/trace').open('a') as f: f.write(sys.argv[1]+'\\n')\n"
        "print(sys.argv[1])\n"
        "sys.exit(9 if sys.argv[1]=='logout' and os.getenv('FAIL_LOGOUT')=='1' else 0)\n")
    data = {"schema_version": 1, "profiles": {"custom-consumer": {"mandatory": ["bootstrap", "logout"], "optional": ["extra"]}},
            "suites": {name: {"argv": [sys.executable, "suite.py", name], "reuse": True,
                              "context": {"files": ["suite.py"], "env": ["FAIL_LOGOUT"] if name == "logout" else []}}
                       for name in ["bootstrap", "logout", "extra"]}}
    (repo / "profiles.json").write_text(json.dumps(data))
    git(repo, "add", ".")
    git(repo, "commit", "-m", "initial")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("FAIL_LOGOUT", "0")
    return repo


def cli(*args):
    result = RUNNER.invoke(app, ["verification", *map(str, args)])
    return result


def receipt(repo, name):
    return json.loads((repo / ".cafe" / name / "verification.json").read_text())


def out(name):
    return f".cafe/{name}/output.md"


def process(repo, *args):
    env = dict(os.environ, PYTHONPATH=SOURCE)
    return subprocess.run([sys.executable, "-c", "from cafe.ui.cli import app; app()", "verification", *map(str, args)],
                          cwd=repo, env=env, text=True, capture_output=True, timeout=30)


def test_configured_acceptance_and_explicit_ci_consume_changed_manifest(project):
    for cycle in range(2):
        if cycle:
            data = json.loads((project / "profiles.json").read_text())
            data["suites"]["logout"]["argv"][-1] = "changed-logout"
            (project / "profiles.json").write_text(json.dumps(data))
            git(project, "add", ".")
            git(project, "commit", "-m", "change entry")
        qa = cli("run", "--output-file", out(f"qa{cycle}"), "--add-suite", "extra")
        assert qa.exit_code == 0, qa.output
        ci = process(project, "run", "--profile-file", "profiles.json", "--profile", "custom-consumer",
                     "--add-suite", "extra", "--output-file", out(f"ci{cycle}"))
        assert ci.returncode == 0, ci.stdout + ci.stderr
        expected = ["bootstrap", "changed-logout" if cycle else "logout", "extra"] * 2
        assert (project / ".cafe/trace").read_text().splitlines()[-6:] == expected
        assert receipt(project, f"qa{cycle}")["selected"] == ["bootstrap", "logout", "extra"]
        assert cli("check", "--output-file", out(f"ci{cycle}"), "--add-suite", "extra").exit_code == 0


def test_partial_pass_cannot_replace_failing_baseline_or_missing_full_results(project, monkeypatch):
    (project / ".cafe/config.yaml").unlink()
    partial = cli("run", "--output-file", out("partial"), "--scope", "targeted", "--", sys.executable, "suite.py", "bootstrap")
    assert partial.exit_code == 0
    monkeypatch.setenv("FAIL_LOGOUT", "1")
    bind = ["--profile-file", "profiles.json", "--profile", "custom-consumer"]
    assert cli("check", "--output-file", out("partial"), *bind).exit_code == 1
    failed = cli("run", "--output-file", out("failed"), *bind)
    assert failed.exit_code == 9, failed.output
    data = receipt(project, "failed")
    assert data["suites"][1]["exit_code"] == 9
    assert (project / ".cafe/trace").read_text().splitlines() == ["bootstrap", "bootstrap", "logout"]
    data["suites"].pop()
    data.update(valid=True, exit_code=0, scope="full", status="passed")
    (project / ".cafe/failed/verification.json").write_text(json.dumps(data))
    assert cli("check", "--output-file", out("failed"), *bind).exit_code == 1


def test_mixed_reuse_keeps_source_history_and_requires_new_context(project, monkeypatch):
    assert cli("run", "--output-file", out("source")).exit_code == 0
    source = project / ".cafe/source/verification.json"
    source_log = source.with_name("verification.log")
    before = source.read_bytes(), source_log.read_bytes()
    assert cli("reuse", "--source-output-file", out("source"), "--output-file", out("reused")).exit_code == 0
    assert receipt(project, "reused")["reused"] == 2
    monkeypatch.setenv("FAIL_LOGOUT", "different-but-still-successful")
    assert cli("reuse", "--source-output-file", out("source"), "--output-file", out("stale")).exit_code != 0
    assert not (project / ".cafe/stale/verification.json").exists()
    mixed = cli("run", "--reuse-from", out("source"), "--output-file", out("mixed"))
    assert mixed.exit_code == 0, mixed.output
    assert [row["action"] for row in receipt(project, "mixed")["suites"]] == ["reused", "executed"]
    assert (source.read_bytes(), source_log.read_bytes()) == before


def test_profile_rejects_replacement_commands_and_invalid_adoption(project):
    assert cli("run", "--output-file", out("narrow"), "--", sys.executable, "-c", "pass").exit_code == 2
    assert cli("run", "--output-file", out("narrow"), "--scope", "targeted").exit_code == 2
    (project / ".cafe/config.yaml").write_text("verification: broken\n")
    assert cli("run", "--output-file", out("invalid")).exit_code == 2


def test_equal_coverage_reuse_eliminates_duplicate_suite_execution(project):
    for name in ["first", "repeat"]:
        assert cli("run", "--output-file", out(name)).exit_code == 0
    assert cli("run", "--output-file", out("cached"), "--reuse-from", out("repeat")).exit_code == 0
    first, repeat, cached = [receipt(project, name) for name in ["first", "repeat", "cached"]]
    assert first["selected"] == repeat["selected"] == cached["selected"] == ["bootstrap", "logout"]
    assert [data["executed"] for data in [first, repeat, cached]] == [2, 2, 0]
    assert cached["reused"] == 2 and cached["pending"] == []
    assert all(data["duration_seconds"] >= 0 and data["valid"] for data in [first, repeat, cached])
    assert len((project / ".cafe/trace").read_text().splitlines()) == 4
