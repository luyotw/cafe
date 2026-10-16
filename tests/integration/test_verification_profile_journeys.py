"""I1/I2/I3/I5/I6: public configured consumers and CI share executable authority."""

import json
import os
import subprocess
import sys
from pathlib import Path

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
    (repo / ".cafe/config.yaml").write_text(
        "verification:\n  profile_file: profiles.json\n  profile: custom-consumer\n"
    )
    (repo / "suite.py").write_text(
        "import os,sys\nfrom pathlib import Path\n"
        "with Path('.cafe/trace').open('a') as f: f.write(sys.argv[1]+'\\n')\n"
        "print(sys.argv[1])\n"
        "sys.exit(9 if sys.argv[1]=='logout' and os.getenv('FAIL_LOGOUT')=='1' else 0)\n"
    )
    data = {
        "schema_version": 1,
        "profiles": {
            "custom-consumer": {"mandatory": ["bootstrap", "logout"], "optional": ["extra"]}
        },
        "suites": {
            name: {
                "argv": [sys.executable, "suite.py", name],
                "reuse": True,
                "context": {
                    "files": ["suite.py"],
                    "env": ["FAIL_LOGOUT"] if name == "logout" else [],
                },
            }
            for name in ["bootstrap", "logout", "extra"]
        },
    }
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
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from cafe.ui.cli import app; app()",
            "verification",
            *map(str, args),
        ],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )


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
        ci = process(
            project,
            "run",
            "--profile-file",
            "profiles.json",
            "--profile",
            "custom-consumer",
            "--add-suite",
            "extra",
            "--output-file",
            out(f"ci{cycle}"),
        )
        assert ci.returncode == 0, ci.stdout + ci.stderr
        expected = ["bootstrap", "changed-logout" if cycle else "logout", "extra"] * 2
        assert (project / ".cafe/trace").read_text().splitlines()[-6:] == expected
        assert receipt(project, f"qa{cycle}")["selected"] == ["bootstrap", "logout", "extra"]
        assert (
            cli("check", "--output-file", out(f"ci{cycle}"), "--add-suite", "extra").exit_code == 0
        )


def test_partial_pass_cannot_replace_failing_baseline_or_missing_full_results(project, monkeypatch):
    (project / ".cafe/config.yaml").unlink()
    partial = cli(
        "run",
        "--output-file",
        out("partial"),
        "--scope",
        "targeted",
        "--",
        sys.executable,
        "suite.py",
        "bootstrap",
    )
    assert partial.exit_code == 0
    monkeypatch.setenv("FAIL_LOGOUT", "1")
    bind = ["--profile-file", "profiles.json", "--profile", "custom-consumer"]
    assert cli("check", "--output-file", out("partial"), *bind).exit_code == 1
    failed = cli("run", "--output-file", out("failed"), *bind)
    assert failed.exit_code == 9, failed.output
    data = receipt(project, "failed")
    assert data["suites"][1]["exit_code"] == 9
    assert (project / ".cafe/trace").read_text().splitlines() == [
        "bootstrap",
        "bootstrap",
        "logout",
    ]
    data["suites"].pop()
    data.update(valid=True, exit_code=0, scope="full", status="passed")
    (project / ".cafe/failed/verification.json").write_text(json.dumps(data))
    assert cli("check", "--output-file", out("failed"), *bind).exit_code == 1


def test_mixed_reuse_keeps_source_history_and_requires_new_context(project, monkeypatch):
    assert cli("run", "--output-file", out("source")).exit_code == 0
    source = project / ".cafe/source/verification.json"
    source_log = source.with_name("verification.log")
    before = source.read_bytes(), source_log.read_bytes()
    assert (
        cli(
            "reuse", "--source-output-file", out("source"), "--output-file", out("reused")
        ).exit_code
        == 0
    )
    assert receipt(project, "reused")["reused"] == 2
    monkeypatch.setenv("FAIL_LOGOUT", "different-but-still-successful")
    assert (
        cli("reuse", "--source-output-file", out("source"), "--output-file", out("stale")).exit_code
        != 0
    )
    assert not (project / ".cafe/stale/verification.json").exists()
    mixed = cli("run", "--reuse-from", out("source"), "--output-file", out("mixed"))
    assert mixed.exit_code == 0, mixed.output
    assert [row["action"] for row in receipt(project, "mixed")["suites"]] == ["reused", "executed"]
    assert (source.read_bytes(), source_log.read_bytes()) == before


def test_profile_rejects_replacement_commands_and_invalid_adoption(project):
    assert (
        cli("run", "--output-file", out("narrow"), "--", sys.executable, "-c", "pass").exit_code
        == 2
    )
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


@pytest.mark.parametrize("dependency", ["file", "TZ", "FROZEN_CLOCK", "SEED", "probe"])
def test_public_reuse_rechecks_real_runtime_dependencies_locally(project, monkeypatch, dependency):
    monkeypatch.setenv("TZ", "Asia/Taipei")
    monkeypatch.setenv("FROZEN_CLOCK", "2026-10-15T15:59:59+00:00")
    monkeypatch.setenv("SEED", "656")
    runtime = project / ".cafe/runtime"
    capability = project / ".cafe/capability"
    runtime.write_text("fixture-a")
    capability.write_text("capability-a")
    path = project / "profiles.json"
    data = json.loads(path.read_text())
    data["suites"]["bootstrap"]["context"] = {
        "files": ["suite.py", ".cafe/runtime"],
        "env": ["TZ", "FROZEN_CLOCK", "SEED"],
        "probes": [
            [
                sys.executable,
                "-c",
                "from pathlib import Path; print(Path('.cafe/capability').read_text())",
            ]
        ],
    }
    path.write_text(json.dumps(data))
    git(project, "add", ".")
    git(project, "commit", "-m", "declare runtime dependencies")
    original = process(project, "run", "--output-file", out("runtime-source"))
    assert original.returncode == 0, original.stdout + original.stderr
    if dependency == "file":
        runtime.write_text("fixture-b")
    elif dependency == "probe":
        capability.write_text("capability-b")
    else:
        monkeypatch.setenv(dependency, "changed")
    assert process(project, "check", "--output-file", out("runtime-source")).returncode == 1
    assert (
        process(
            project,
            "reuse",
            "--source-output-file",
            out("runtime-source"),
            "--output-file",
            out("invalid"),
        ).returncode
        == 1
    )
    result = process(
        project, "run", "--reuse-from", out("runtime-source"), "--output-file", out("runtime-mixed")
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert [row["action"] for row in receipt(project, "runtime-mixed")["suites"]] == [
        "executed",
        "reused",
    ]


@pytest.mark.parametrize("schema", [1, 2])
@pytest.mark.parametrize("config", [None, "", "# no profile adoption\n"])
def test_nonadopter_public_legacy_check_reuse_focus_preserve_historical_claims(
    project, schema, config
):
    config_path = project / ".cafe/config.yaml"
    if config is None:
        config_path.unlink()
    else:
        config_path.write_text(config)
    (project / "test_legacy.py").write_text("def test_existing():\n    assert 2 + 2 == 4\n")
    git(project, "add", ".")
    git(project, "commit", "-m", "legacy check")
    result = cli(
        "run",
        "--output-file",
        out("legacy"),
        "--",
        "python3",
        "-m",
        "pytest",
        "-q",
        "--no-cov",
    )
    assert result.exit_code == 0, result.output
    path = project / ".cafe/legacy/verification.json"
    data = json.loads(path.read_text())
    data["schema_version"] = schema
    if schema == 1:
        data.pop("output_log")
    path.write_text(json.dumps(data))
    assert cli("check", "--output-file", out("legacy")).exit_code == 0
    assert (
        cli(
            "reuse", "--source-output-file", out("legacy"), "--output-file", out("old-reused")
        ).exit_code
        == 0
    )
    focused = cli("focus", "--output-file", out("old-reused"), "--", "test_legacy.py")
    assert focused.exit_code == 0, focused.output
    assert (
        cli(
            "check",
            "--output-file",
            out("old-reused"),
            "--profile-file",
            "profiles.json",
            "--profile",
            "custom-consumer",
        ).exit_code
        == 1
    )
    assert (
        cli(
            "run", "--output-file", out("legacy-fail"), "--", sys.executable, "-c", "exit(6)"
        ).exit_code
        == 6
    )


@pytest.mark.parametrize("mutator_first", [True, False])
def test_mixed_execution_cannot_reuse_context_invalidated_during_the_run(
    project, monkeypatch, mutator_first
):
    runtime = project / ".cafe/runtime"
    runtime.write_text("good")
    path = project / "profiles.json"
    data = json.loads(path.read_text())
    mutator = "bootstrap" if mutator_first else "logout"
    dependent = "logout" if mutator_first else "bootstrap"
    data["suites"][mutator] = {
        "argv": [
            sys.executable,
            "-c",
            "import os; from pathlib import Path; "
            "Path('.cafe/runtime').write_text('bad') if os.environ['MODE']=='mutate' else None",
        ],
        "reuse": True,
        "context": {"env": ["MODE"]},
    }
    data["suites"][dependent] = {
        "argv": [
            sys.executable,
            "-c",
            "from pathlib import Path; assert Path('.cafe/runtime').read_text()=='good'",
        ],
        "reuse": True,
        "context": {"files": [".cafe/runtime"]},
    }
    path.write_text(json.dumps(data))
    git(project, "add", ".")
    git(project, "commit", "-m", "context mutation fixture")
    monkeypatch.setenv("MODE", "stable")
    assert process(project, "run", "--output-file", out("stable")).returncode == 0
    before = (project / ".cafe/stable/verification.json").read_bytes()
    monkeypatch.setenv("MODE", "mutate")
    mixed = process(project, "run", "--reuse-from", out("stable"), "--output-file", out("mutated"))
    assert mixed.returncode != 0, mixed.stdout + mixed.stderr
    result = receipt(project, "mutated")
    assert not result["valid"]
    if mutator_first:
        assert result["status"] == "failed"
        assert [row["action"] for row in result["suites"]] == ["executed", "executed"]
        assert result["suites"][1]["exit_code"] == 1
    else:
        assert result["status"] == "incomplete"
        assert dependent in result["pending"]
    assert (project / ".cafe/stable/verification.json").read_bytes() == before


@pytest.mark.parametrize(
    "config", ["verification:\n", "verification: {}\n", "verification: broken\n"]
)
def test_malformed_explicit_adoption_never_executes_a_legacy_fallback(project, config):
    (project / ".cafe/config.yaml").write_text(config)
    for args in [
        ["run", "--output-file", out("invalid"), "--", sys.executable, "suite.py", "bootstrap"],
        ["check", "--output-file", out("invalid")],
        ["reuse", "--source-output-file", out("invalid"), "--output-file", out("reused")],
    ]:
        result = process(project, *args)
        assert result.returncode == 2
    assert not (project / ".cafe/trace").exists()


@pytest.mark.parametrize("reuse", [False, True])
@pytest.mark.parametrize("dependency", ["env", "file", "probe"])
def test_fresh_missing_context_passes_check_but_reuse_rejects_missing_and_changed_context(
    project, monkeypatch, reuse, dependency
):
    monkeypatch.delenv("OPTIONAL_REUSE_CONTEXT", raising=False)
    manifest_path = project / "profiles.json"
    data = json.loads(manifest_path.read_text())
    data["profiles"]["custom-consumer"] = {"mandatory": ["bootstrap"], "optional": []}
    suite = data["suites"]["bootstrap"]
    suite["reuse"] = reuse
    if dependency == "env":
        suite["context"] = {"env": ["OPTIONAL_REUSE_CONTEXT"]}
    elif dependency == "file":
        suite["context"] = {"files": [".cafe/reuse-input"]}
    else:
        suite["context"] = {
            "probes": [
                [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; "
                    "exit(0 if Path('.cafe/reuse-input').exists() else 4)",
                ]
            ]
        }
    manifest_path.write_text(json.dumps(data))
    git(project, "add", ".")
    git(project, "commit", "-m", "context evidence fixture")
    fresh = process(project, "run", "--output-file", out("fresh"))
    assert fresh.returncode == 0, fresh.stdout + fresh.stderr
    checked = process(project, "check", "--output-file", out("fresh"))
    assert checked.returncode == 0, checked.stdout + checked.stderr
    for public_result in [json.loads(fresh.stdout), json.loads(checked.stdout)]:
        assert public_result["valid"] and public_result["status"] == "passed"
        assert public_result["pending"] == []
        row = public_result["suites"][0]
        assert row["completion"] == "completed" and row["exit_code"] == 0
        assert row["context_errors"] and not row["reusable"] and row["reuse_reasons"]
    receipt_path = project / ".cafe/fresh/verification.json"
    log_path = receipt_path.with_name("verification.log")
    before = receipt_path.read_bytes(), log_path.read_bytes()
    trace = project / ".cafe/trace"
    executed = trace.read_bytes()
    refused = process(
        project, "reuse", "--source-output-file", out("fresh"), "--output-file", out("refused")
    )
    assert refused.returncode == 1
    assert not json.loads(refused.stdout)["valid"]
    assert not (project / ".cafe/refused/verification.json").exists()
    assert trace.read_bytes() == executed
    # An explicit mixed run may execute a non-reusable row; reuse-only cannot.
    mixed = process(project, "run", "--reuse-from", out("fresh"), "--output-file", out("mixed"))
    assert mixed.returncode == 0, mixed.stdout + mixed.stderr
    assert json.loads(mixed.stdout)["executed"] == 1 and json.loads(mixed.stdout)["reused"] == 0
    assert process(project, "check", "--output-file", out("mixed")).returncode == 0
    if dependency == "env":
        monkeypatch.setenv("OPTIONAL_REUSE_CONTEXT", "now-available")
    else:
        (project / ".cafe/reuse-input").write_text("now-available")
    executed = trace.read_bytes()
    assert process(project, "check", "--output-file", out("fresh")).returncode == 1
    assert (
        process(
            project, "reuse", "--source-output-file", out("fresh"), "--output-file", out("stale")
        ).returncode
        == 1
    )
    assert trace.read_bytes() == executed
    assert (receipt_path.read_bytes(), log_path.read_bytes()) == before
    assert process(project, "run", "--output-file", out("available")).returncode == 0
    available = process(project, "check", "--output-file", out("available"))
    assert available.returncode == 0
    assert json.loads(available.stdout)["suites"][0]["reusable"] == reuse
