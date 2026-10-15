"""I5/delivery correction: deployment evidence must contain the actual integration."""

import hashlib
import importlib.util
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = (
    Path(__file__).parents[2]
    / "src/cafe/data/skills/cafe-deliver_development/scripts/verify_github_release_deployment.py"
)
spec = importlib.util.spec_from_file_location("release_deployment_tool", SCRIPT)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)
COMMIT = "c" * 40
DEPLOYED = "d" * 40
REPO = "owner/repo"
WORKFLOW = ".github/workflows/publish-pypi.yml"


def options():
    return {
        "environment": "pypi",
        "workflow": WORKFLOW,
        "deployment_job": "Publish distributions to PyPI",
        "jobs": {
            "Build distributions": ["Verify release identity", "Build and inspect distributions"],
            "Publish distributions to PyPI": ["Publish distributions"],
        },
    }


def inputs(commit=COMMIT):
    return {"repository": REPO, "target_branch": "develop", "commit": commit, "options": options()}


def verification_plan():
    return {
        "scope": "Require a successful PyPI release deployment containing the actual merge commit",
        "tool": {
            "capability": "cafe.delivery.verify",
            "owner": "cafe-deliver_development",
            "path": "scripts/verify_github_release_deployment.py",
            "sha256": hashlib.sha256(SCRIPT.read_bytes()).hexdigest(),
            "options": options(),
        },
    }


def responses(sha=DEPLOYED):
    jobs = []
    for index, (name, steps) in enumerate(options()["jobs"].items(), start=9):
        jobs.append(
            {
                "id": index,
                "name": name,
                "run_id": 42,
                "run_attempt": 1,
                "head_sha": sha,
                "status": "completed",
                "conclusion": "success",
                "steps": [
                    {"number": n, "name": step, "status": "completed", "conclusion": "success"}
                    for n, step in enumerate(steps, start=1)
                ],
            }
        )
    return {
        "repos/owner/repo/deployments?environment=pypi&per_page=1": [
            {
                "id": 7,
                "sha": sha,
                "ref": "v1.2.3",
                "environment": "pypi",
                "repository_url": "https://api.github.com/repos/owner/repo",
            }
        ],
        "repos/owner/repo/compare/"
        + COMMIT
        + "..."
        + sha
        + "?per_page=1": {
            "status": "ahead",
            "ahead_by": 1,
            "behind_by": 0,
            "base_commit": {"sha": COMMIT},
            "merge_base_commit": {"sha": COMMIT},
        },
        "repos/owner/repo/deployments/7/statuses?per_page=1": [
            {
                "id": 8,
                "state": "success",
                "environment": "pypi",
                "deployment_url": "https://api.github.com/repos/owner/repo/deployments/7",
                "environment_url": "https://pypi.org/p/cafe-engine",
                "log_url": "https://github.com/owner/repo/actions/runs/42/job/10",
            }
        ],
        "repos/owner/repo/releases/tags/v1.2.3": {
            "id": 99,
            "tag_name": "v1.2.3",
            "draft": False,
            "published_at": "2026-10-15T12:00:00Z",
        },
        "repos/owner/repo/commits/tags%2Fv1.2.3": {"sha": sha},
        "repos/owner/repo/actions/runs/42": {
            "id": 42,
            "run_attempt": 1,
            "event": "release",
            "path": WORKFLOW,
            "head_sha": sha,
            "head_branch": "v1.2.3",
            "repository": {"full_name": REPO},
            "status": "completed",
            "conclusion": "success",
        },
        "repos/owner/repo/actions/runs/42/attempts/1/jobs?per_page=100": {
            "total_count": 2,
            "jobs": jobs,
        },
    }


def install(monkeypatch, data=None, error=None):
    data = responses() if data is None else data
    calls = []

    def api(self, endpoint):
        calls.append(endpoint)
        if error:
            raise error
        return deepcopy(data[endpoint])

    monkeypatch.setattr(tool.Commands, "api", api)
    return calls


@pytest.mark.parametrize("sha", [COMMIT, DEPLOYED])
def test_exact_or_descendant_release_is_commit_linked_and_stable(monkeypatch, sha):
    install(monkeypatch, responses(sha))
    result = tool.observe(inputs())
    assert result["state"] == "succeeded" and result["commit"] == COMMIT
    assert result["evidence"]["deployment"]["sha"] == sha
    assert result["evidence"]["release"]["tag"] == "v1.2.3"
    assert result["evidence"]["workflow"]["run_id"] == 42
    assert tool.observe(inputs()) == result


@pytest.mark.parametrize("missing", ["deployments", "statuses", "release", "run"])
def test_missing_matching_observation_remains_pending(monkeypatch, missing):
    data = responses()
    key = {
        "deployments": "repos/owner/repo/deployments?environment=pypi&per_page=1",
        "statuses": "repos/owner/repo/deployments/7/statuses?per_page=1",
        "release": "repos/owner/repo/releases/tags/v1.2.3",
        "run": "repos/owner/repo/actions/runs/42",
    }[missing]
    data[key] = [] if missing in {"deployments", "statuses"} else None
    install(monkeypatch, data)
    assert tool.observe(inputs())["state"] == "pending"


@pytest.mark.parametrize("relation", ["behind", "diverged"])
def test_older_or_unrelated_deployment_never_satisfies_the_merge(monkeypatch, relation):
    data = responses()
    data["repos/owner/repo/compare/" + COMMIT + "..." + DEPLOYED + "?per_page=1"].update(
        status=relation, merge_base_commit={"sha": "a" * 40}
    )
    calls = install(monkeypatch, data)
    assert tool.observe(inputs())["state"] == "pending"
    assert not any("/statuses" in endpoint for endpoint in calls)


def test_previously_passing_deployment_does_not_verify_a_later_merge(monkeypatch):
    data = responses(COMMIT)
    install(monkeypatch, data)
    assert tool.observe(inputs())["state"] == "succeeded"
    later = "e" * 40
    data["repos/owner/repo/compare/" + later + "..." + COMMIT + "?per_page=1"] = {
        "status": "behind",
        "base_commit": {"sha": later},
        "merge_base_commit": {"sha": COMMIT},
    }
    install(monkeypatch, data)
    assert tool.observe(inputs(later))["state"] == "pending"


@pytest.mark.parametrize(
    "state,expected",
    [
        ("queued", "pending"),
        ("pending", "pending"),
        ("in_progress", "pending"),
        ("inactive", "pending"),
        ("failure", "failed"),
        ("error", "failed"),
    ],
)
def test_deployment_state_preserves_real_failure(monkeypatch, state, expected):
    data = responses()
    data["repos/owner/repo/deployments/7/statuses?per_page=1"][0]["state"] = state
    install(monkeypatch, data)
    assert tool.observe(inputs())["state"] == expected


@pytest.mark.parametrize(
    "status,conclusion,expected",
    [
        ("queued", None, "pending"),
        ("in_progress", None, "pending"),
        ("completed", "failure", "failed"),
        ("completed", "cancelled", "failed"),
    ],
)
def test_release_workflow_must_complete_successfully(monkeypatch, status, conclusion, expected):
    data = responses()
    data["repos/owner/repo/actions/runs/42"].update(status=status, conclusion=conclusion)
    install(monkeypatch, data)
    assert tool.observe(inputs())["state"] == expected


@pytest.mark.parametrize(
    "change",
    ["missing_job", "duplicate_job", "failed_job", "missing_step", "failed_step", "skipped_step"],
)
def test_run_success_cannot_hide_missing_or_unsuccessful_required_checks(monkeypatch, change):
    data = responses()
    jobs = data["repos/owner/repo/actions/runs/42/attempts/1/jobs?per_page=100"]
    if change == "missing_job":
        jobs["jobs"].pop()
        jobs["total_count"] -= 1
    elif change == "duplicate_job":
        jobs["jobs"].append(deepcopy(jobs["jobs"][0]))
        jobs["total_count"] += 1
    elif change == "failed_job":
        jobs["jobs"][0]["conclusion"] = "failure"
    elif change == "missing_step":
        jobs["jobs"][0]["steps"].pop()
    else:
        jobs["jobs"][0]["steps"][0]["conclusion"] = (
            "failure" if change == "failed_step" else "skipped"
        )
    install(monkeypatch, data)
    assert tool.observe(inputs())["state"] == "failed"


@pytest.mark.parametrize(
    "change",
    [
        "environment",
        "repository",
        "run_sha",
        "run_tag",
        "run_event",
        "workflow",
        "tag_sha",
        "release_tag",
        "job_sha",
        "job_run",
        "truncated_jobs",
        "foreign_log",
    ],
)
def test_identity_or_incomplete_evidence_never_passes(monkeypatch, change):
    data = responses()
    run = data["repos/owner/repo/actions/runs/42"]
    if change == "environment":
        data["repos/owner/repo/deployments?environment=pypi&per_page=1"][0]["environment"] = "other"
    elif change == "repository":
        run["repository"]["full_name"] = "other/repo"
    elif change == "run_sha":
        run["head_sha"] = "a" * 40
    elif change == "run_tag":
        run["head_branch"] = "v0.0.1"
    elif change == "run_event":
        run["event"] = "push"
    elif change == "workflow":
        run["path"] = ".github/workflows/other.yml"
    elif change == "tag_sha":
        data["repos/owner/repo/commits/tags%2Fv1.2.3"]["sha"] = "a" * 40
    elif change == "release_tag":
        data["repos/owner/repo/releases/tags/v1.2.3"]["tag_name"] = "v0.0.1"
    elif change == "foreign_log":
        data["repos/owner/repo/deployments/7/statuses?per_page=1"][0][
            "log_url"
        ] = "https://github.com/other/repo/actions/runs/42/job/10"
    else:
        jobs = data["repos/owner/repo/actions/runs/42/attempts/1/jobs?per_page=100"]
        if change == "job_sha":
            jobs["jobs"][0]["head_sha"] = "a" * 40
        elif change == "job_run":
            jobs["jobs"][0]["run_id"] = 99
        else:
            jobs["total_count"] += 1
    install(monkeypatch, data)
    result = tool.observe(inputs())
    assert result["state"] == "unknown" and not result["retryable"]


def test_old_deployment_job_does_not_satisfy_new_run_attempt(monkeypatch):
    data = responses()
    data["repos/owner/repo/actions/runs/42"]["run_attempt"] = 2
    jobs = data.pop("repos/owner/repo/actions/runs/42/attempts/1/jobs?per_page=100")
    for job in jobs["jobs"]:
        job.update(id=job["id"] + 10, run_attempt=2)
    data["repos/owner/repo/actions/runs/42/attempts/2/jobs?per_page=100"] = jobs
    install(monkeypatch, data)
    assert tool.observe(inputs())["state"] == "pending"


@pytest.mark.parametrize(
    "extra", [{"commit": "a" * 40}, {"repository": "other/repo"}, {"environment": ""}, {"jobs": {}}]
)
def test_options_cannot_replace_host_identity_or_remove_required_checks(monkeypatch, extra):
    request = inputs()
    request["options"].update(extra)
    calls = install(monkeypatch)
    assert tool.observe(request)["state"] == "unknown" and not calls


@pytest.mark.parametrize(
    "message,retryable",
    [
        ("HTTP 401", False),
        ("HTTP 403: permission denied", False),
        ("HTTP 403: rate limit exceeded", True),
        ("HTTP 429", True),
        ("HTTP 503", True),
        ("connection timeout", True),
    ],
)
def test_real_transport_only_uses_get_and_classifies_transient_errors(
    monkeypatch, message, retryable
):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=1, stderr=message, stdout="")

    monkeypatch.setattr(tool.subprocess, "run", run)
    with pytest.raises(tool.ObservationError) as exc:
        tool.Commands(10).api("repos/owner/repo/deployments")
    assert exc.value.retryable is retryable
    assert calls[0][-2:] == ["--method", "GET"]
    assert calls[0][calls[0].index("--hostname") + 1] == "github.com"
    install(monkeypatch, error=exc.value)
    result = tool.observe(inputs())
    assert result["state"] == "unknown" and result["retryable"] is retryable


@pytest.mark.parametrize("resource", ["releases/tags/v1.2.3", "actions/runs/42"])
def test_eventual_missing_api_resource_is_pending(monkeypatch, resource):
    data = responses()

    def api(self, endpoint):
        if endpoint == "repos/owner/repo/" + resource:
            raise tool.ObservationError("github_query_failed:404", status=404)
        return deepcopy(data[endpoint])

    monkeypatch.setattr(tool.Commands, "api", api)
    assert tool.observe(inputs())["state"] == "pending"


@pytest.mark.parametrize("failure,retryable", [("timeout", True), ("missing", False)])
def test_process_timeout_and_missing_github_tool_are_not_success(monkeypatch, failure, retryable):
    def run(*args, **kwargs):
        if failure == "timeout":
            raise tool.subprocess.TimeoutExpired("gh", kwargs["timeout"])
        raise FileNotFoundError("gh")

    monkeypatch.setattr(tool.subprocess, "run", run)
    result = tool.observe(inputs())
    assert result["state"] == "unknown" and result["retryable"] is retryable
