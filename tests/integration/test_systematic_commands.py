"""Public preparation and zero-model execution with real bounded child processes."""

import json
import os
import subprocess
import sys

import pytest
import yaml
from typer.testing import CliRunner

from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
from cafe.playbooks.loader import PlaybookLoader
from cafe.ui.cli import app
from tests.conftest import create_minimal_config


def setup(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    create_minimal_config(tmp_path)
    for args in (
        ("init", "-b", "main"),
        ("config", "user.name", "Test"),
        ("config", "user.email", "test@example.invalid"),
        ("commit", "--allow-empty", "-m", "Initial"),
    ):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    command = [
        sys.executable,
        "-c",
        "from pathlib import Path; import sys; Path('observed').write_text(sys.argv[1])",
        {"config": "export_job.message"},
    ]
    data = {
        "playbook": {
            "id": "systematic-example",
            "applicability": {
                "summary": "Run a prepared local command",
                "use_when": ["A local export is required"],
                "avoid_when": ["External delivery is required"],
            },
        },
        "skills": {"workflow": {"shared": []}, "chat": {"shared": []}},
        "commands": {
            "prepare": {
                "fields": [
                    {
                        "id": "message",
                        "type": "text",
                        "label": "Message",
                        "write": "export_job.message",
                        "required": True,
                    }
                ]
            }
        },
        "steps": {
            "export_job": {
                "assignee_type": "auto",
                "automatic": {"executor": "command", "inputs": {"argv": command}},
                "on": {"await_agent": "_done"},
            }
        },
    }
    catalog = tmp_path / ".cafe/playbooks"
    catalog.mkdir(parents=True, exist_ok=True)
    (catalog / "systematic-example.yaml").write_text(yaml.safe_dump(data))
    monkeypatch.setattr("cafe.ui.commands.lifecycle._offer_cli_update", lambda **kw: None)
    # This test substitute is the process boundary, not an alternate production backend.
    binary = tmp_path / "bin"
    binary.mkdir()
    fake = binary / "codex"
    fake.write_text(
        "#!" + sys.executable + "\nimport os,sys\na=sys.argv[2:]\ni=0\n"
        "while i<len(a):\n"
        " if a[i] in ('--sandbox-state-json','--sandbox-state-readable-root'): i+=2\n"
        " elif a[i].startswith('--sandbox-state-'): i+=1\n"
        " else: break\n"
        "assert '--sandbox-state-disable-network' in sys.argv\n"
        "assert not any('TOKEN' in k for k in os.environ)\n"
        "os.execvp(a[i],a[i:])\n"
    )
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(binary) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("AN_EXAMPLE_TOKEN", "must-not-reach-command")
    return data


def prepared(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    result = CliRunner().invoke(
        app,
        [
            "prepare",
            "command-example",
            "--playbook",
            "systematic-example",
            "--no-interactive",
            "--no-check",
            "--set",
            "message=one value; $(literal)",
        ],
    )
    assert result.exit_code == 0, result.output + repr(result.exception)
    issue = tmp_path / ".cafe/issues/command-example"
    config = yaml.safe_load((issue / "issue.yaml").read_text())
    assert config["export_job"]["message"] == "one value; $(literal)"
    data = PlaybookLoader(project_root=tmp_path, global_root=tmp_path / "global").load(
        "systematic-example", strict=True
    )
    return issue, data


def runtime(issue, data):
    return BlackboardWorkflowRuntime(
        issue_dir=issue,
        playbook=data,
        executor=lambda *a, **kw: pytest.fail("systematic step invoked an agent"),
    )


def test_prepare_and_command_use_declared_values_without_a_model(tmp_path, monkeypatch):
    issue, data = prepared(tmp_path, monkeypatch)
    result = runtime(issue, data).run()
    assert result.completed
    assert (tmp_path / "observed").read_text() == "one value; $(literal)"
    receipts = list((issue / "export_job/systematic").glob("*.json"))
    assert len(receipts) == 1
    assert json.loads(receipts[0].read_text())["returncode"] == 0


def test_public_workflow_runs_without_phase_models(tmp_path, monkeypatch):
    issue, data = prepared(tmp_path, monkeypatch)
    result = CliRunner().invoke(
        app,
        ["workflow", "--execute", "--issue", "command-example", "--playbook", "systematic-example"],
    )
    assert result.exit_code == 0, result.output + repr(result.exception)
    assert (tmp_path / "observed").exists(), result.output
    assert (tmp_path / "observed").read_text() == "one value; $(literal)"


def test_public_workflow_advances_consecutive_system_steps(tmp_path, monkeypatch):
    issue, data = prepared(tmp_path, monkeypatch)
    data["steps"]["export_job"]["on"]["await_agent"] = "finish_export"
    from copy import deepcopy
    data["steps"]["finish_export"] = deepcopy(data["steps"]["export_job"])
    data["steps"]["finish_export"]["on"]["await_agent"] = "_done"
    data["steps"]["finish_export"]["automatic"]["inputs"]["argv"][-1] = "second step"
    path = tmp_path / ".cafe/playbooks/systematic-example.yaml"
    original = yaml.safe_load(path.read_text())
    original["steps"] = data["steps"]
    path.write_text(yaml.safe_dump(original))
    result = CliRunner().invoke(app, ["workflow", "--execute", "--issue", issue.name,
                                    "--playbook", "systematic-example"])
    assert result.exit_code == 0, result.output
    assert "Workflow completed" in result.output
    assert (tmp_path / "observed").read_text() == "second step"


@pytest.mark.parametrize("value", ["unknown=foo", "message=first", "message=second"])
def test_invalid_prepare_values_do_not_create_a_branch(tmp_path, monkeypatch, value):
    setup(tmp_path, monkeypatch)
    args = [
        "prepare",
        "bad",
        "--playbook",
        "systematic-example",
        "--no-interactive",
        "--no-check",
        "--set",
        value,
    ]
    if value.startswith("message="):
        args += ["--set", value]
    result = CliRunner().invoke(app, args)
    assert result.exit_code != 0
    assert (
        subprocess.run(
            ["git", "branch", "--show-current"], capture_output=True, text=True
        ).stdout.strip()
        == "main"
    )
    assert not (tmp_path / ".cafe/issues/bad/issue.yaml").exists()


def test_completed_command_survives_interruption_before_workflow_transition(tmp_path, monkeypatch):
    issue, data = prepared(tmp_path, monkeypatch)
    first = runtime(issue, data)

    def crash(**kw):
        raise KeyboardInterrupt()

    monkeypatch.setattr(first, "_complete_owned_transition", crash)
    with pytest.raises(KeyboardInterrupt):
        first.run()
    (tmp_path / "observed").write_text("Do not execute again")
    assert runtime(issue, data).run().completed
    assert (tmp_path / "observed").read_text() == "Do not execute again"


def test_unknown_launch_is_not_replayed_and_input_drift_is_rejected(tmp_path, monkeypatch):
    issue, data = prepared(tmp_path, monkeypatch)
    from cafe.core import automatic_commands

    original = automatic_commands.subprocess.Popen

    def crash(*a, **kw):
        raise KeyboardInterrupt()

    # The sandbox preflight uses subprocess.run, so retain its real child execution.
    monkeypatch.setattr(
        automatic_commands, "preflight_sandbox", lambda *a: type("Check", (), {"available": True})()
    )
    monkeypatch.setattr(automatic_commands.subprocess, "Popen", crash)
    with pytest.raises(KeyboardInterrupt):
        runtime(issue, data).run()
    monkeypatch.setattr(automatic_commands.subprocess, "Popen", original)
    result = runtime(issue, data).run()
    assert "unknown" in result.detail
    assert not (tmp_path / "observed").exists()
    config = yaml.safe_load((issue / "issue.yaml").read_text())
    config["export_job"]["message"] = "changed"
    (issue / "issue.yaml").write_text(yaml.safe_dump(config))
    assert "inputs changed" in runtime(issue, data).run().detail


def test_missing_sandbox_never_falls_back_to_host_execution(tmp_path, monkeypatch):
    issue, data = prepared(tmp_path, monkeypatch)
    monkeypatch.setattr("cafe.core.sandbox_execution.shutil.which", lambda *a: None)
    result = runtime(issue, data).run()
    assert "sandbox_backend_unavailable" in result.detail
    assert not (tmp_path / "observed").exists()


def test_self_loop_commits_visit_retirement_with_its_transition(tmp_path, monkeypatch):
    issue, data = prepared(tmp_path, monkeypatch)
    data["steps"]["export_job"]["on"]["await_agent"] = "export_job"
    first = runtime(issue, data)
    transition = first._complete_owned_transition

    def crash_after_transition(**kw):
        transition(**kw)
        raise KeyboardInterrupt()

    monkeypatch.setattr(first, "_complete_owned_transition", crash_after_transition)
    with pytest.raises(KeyboardInterrupt):
        first.run()
    (tmp_path / "observed").write_text("Next visit must execute")
    second = runtime(issue, data)
    monkeypatch.setattr(
        second,
        "_complete_owned_transition",
        lambda **kw: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    with pytest.raises(KeyboardInterrupt):
        second.run()
    assert (tmp_path / "observed").read_text() == "one value; $(literal)"
    assert len(list((issue / "export_job/systematic").glob("*.json"))) == 2
