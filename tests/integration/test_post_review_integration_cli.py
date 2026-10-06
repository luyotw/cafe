"""Public custom-topology CLI journeys, no-resume reports and read-only status."""

import json

import pytest
from typer.testing import CliRunner

from cafe.ui.cli import app
from tests.integration.integration_fixture import create_journey

runner = CliRunner()


@pytest.fixture
def journey(tmp_path, monkeypatch):
    return create_journey(tmp_path / "cli-journey", monkeypatch)


def invoke(*args):
    result = runner.invoke(app, list(args))
    if isinstance(result.exception, AssertionError):
        raise result.exception
    assert result.exit_code == 0, (result.stdout, result.exception)
    return result


def task_complete(journey, decision):
    task = journey.pending()
    return invoke(
        "task",
        "complete",
        task.id,
        "--result",
        json.dumps(dict(task=task.policy_id, human_task_id=task.id, decision=decision)),
        "--no-resume",
    )


@pytest.mark.parametrize("target", ["local_branch", "github_pr"])
def test_custom_catalog_cli_report_verification_and_recovery(journey, monkeypatch, target):
    monkeypatch.setattr(
        "cafe.utils.github.GitHubOps.observe_integration", lambda *a: dict(journey.observation)
    )
    # Irrelevant familiar artifacts must never gain precedence over declared identities.
    (journey.issue_dir / "workspace.json").write_text('{"head_sha":"misleading"}')
    task_complete(journey, "ship")
    args = [
        "integration",
        "select",
        "--issue",
        "delivery",
        "--target",
        target,
        "--repository",
        str(journey.root) if target == "local_branch" else "owner/repo",
        "--target-branch",
        "main",
        "--json",
    ]
    if target == "local_branch":
        args += ["--feature-branch", "feature"]
    else:
        # Add the fixture's declared publication association before public selection.
        journey.select("github_pr")
        args += ["--pr", "17"]
    selected = json.loads(invoke(*args).stdout)
    assert selected["status"]["state"] == "pending_confirmation"
    assert "confirm" in journey.pending().continuations
    task_complete(journey, "confirm")
    invoke("workflow", "--issue", "delivery", "--execute")
    task_complete(journey, "performed")
    status = json.loads(invoke("integration", "status", "--issue", "delivery", "--json").stdout)
    assert status["state"] == "reported_pending_verification"
    assert journey.state().current_step != "done"
    failure = runner.invoke(app, ["integration", "verify", "--issue", "delivery", "--json"])
    assert failure.exit_code != 0 and not json.loads(failure.stdout)["attempt"]["success"]
    journey.human_integrate(target)
    assert json.loads(invoke("integration", "verify", "--issue", "delivery", "--json").stdout)[
        "attempt"
    ]["success"]
    invoke("workflow", "--issue", "delivery", "--execute")
    final = json.loads(invoke("integration", "status", "--issue", "delivery", "--json").stdout)
    assert final["state"] == "verified" and final["completed"]
    assert journey.state().current_step == "done"
    invoke("workflow", "--issue", "delivery", "--execute")


def test_status_never_inspects_or_reconciles_and_corruption_fails_closed(journey, monkeypatch):
    task_complete(journey, "ship")
    journey.select()
    before = {p: p.read_bytes() for p in journey.issue_dir.rglob("*") if p.is_file()}

    def prohibited(*args, **kwargs):
        raise AssertionError("status must not inspect Git or GitHub")

    monkeypatch.setattr("cafe.core.git.subprocess.run", prohibited)
    result = invoke("integration", "status", "--issue", "delivery", "--json")
    assert json.loads(result.stdout)["state"] == "pending_confirmation"
    assert before == {p: p.read_bytes() for p in journey.issue_dir.rglob("*") if p.is_file()}
    journey.service().records.file_path.write_text('{"schema_version":99}')
    result = runner.invoke(app, ["integration", "status", "--issue", "delivery", "--json"])
    assert result.exit_code != 0 and json.loads(result.stdout)["state"] != "verified"
