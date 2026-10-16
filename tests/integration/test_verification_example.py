"""I4/I6: real deterministic fixtures cross midnight and force notification order."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLE = Path(__file__).resolve().parents[2] / "examples/verification_profiles"
SOURCE = str(Path(__file__).resolve().parents[2] / "src")


def prepare_example(tmp_path):
    repo = tmp_path / "example"
    shutil.copytree(EXAMPLE, repo)
    for args in [
        ["init"],
        ["config", "user.email", "test@example.com"],
        ["config", "user.name", "Test"],
        ["add", "."],
        ["commit", "-m", "example"],
    ]:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    (repo / ".cafe").mkdir()
    shutil.copyfile(repo / "project-config.yaml", repo / ".cafe/config.yaml")
    return repo


def environment():
    return dict(
        os.environ,
        PYTHONPATH=SOURCE,
        TZ="Asia/Taipei",
        FROZEN_CLOCK="2026-10-15T15:59:59+00:00",
        SEED="656",
        BARRIER="notification-before-response",
        FAULT="none",
    )


def invoke(repo, env, *args):
    return subprocess.run(
        [sys.executable, "-c", "from cafe.ui.cli import app; app()", "verification", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_actual_profile_proves_midnight_transition_and_notification_order(tmp_path):
    repo = prepare_example(tmp_path)
    result = invoke(repo, environment(), "run", "--output-file", ".cafe/good/output.md")
    assert result.returncode == 0, result.stdout + result.stderr
    log = (repo / ".cafe/good/verification.log").read_text()
    records = [json.loads(line) for line in log.splitlines()]
    midnight = next(row for row in records if row["suite"] == "midnight")
    assert midnight["before"] == "2026-10-15" and midnight["after"] == "2026-10-16"
    race = next(row for row in records if row["suite"] == "notification")
    assert race["order"] == ["notification", "response"]
    assert race["visible_ids"] == race["persisted_ids"] and len(race["visible_ids"]) == 1
    assert (
        invoke(repo, environment(), "check", "--output-file", ".cafe/good/output.md").returncode
        == 0
    )


@pytest.mark.parametrize("fault", ["midnight", "notification"])
def test_deliberately_faulty_fixture_fails_through_public_profile(tmp_path, fault):
    repo = prepare_example(tmp_path)
    env = dict(environment(), FAULT=fault)
    result = invoke(repo, env, "run", "--output-file", ".cafe/fault/output.md")
    assert result.returncode != 0
    data = json.loads((repo / ".cafe/fault/verification.json").read_text())
    assert data["status"] == "failed"
    assert next(row for row in data["suites"] if row["id"] == fault)["exit_code"] != 0


def test_example_reuse_keeps_coverage_and_control_changes_are_local(tmp_path):
    repo = prepare_example(tmp_path)
    env = environment()
    for name in ["first", "repeat"]:
        result = invoke(repo, env, "run", "--output-file", f".cafe/{name}/output.md")
        assert result.returncode == 0, result.stdout + result.stderr
    result = invoke(
        repo,
        env,
        "run",
        "--reuse-from",
        ".cafe/repeat/output.md",
        "--output-file",
        ".cafe/cached/output.md",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads((repo / ".cafe/cached/verification.json").read_text())
    assert data["executed"] == 0 and data["reused"] == 4 and data["pending"] == []
    changed = dict(env, SEED="657")
    result = invoke(
        repo,
        changed,
        "run",
        "--reuse-from",
        ".cafe/repeat/output.md",
        "--output-file",
        ".cafe/changed/output.md",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    data = json.loads((repo / ".cafe/changed/verification.json").read_text())
    assert {row["id"] for row in data["suites"] if row["action"] == "executed"} == {"notification"}
