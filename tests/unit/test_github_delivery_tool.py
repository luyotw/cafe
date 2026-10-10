"""Provider semantics belong to the optional phase-owned GitHub tool."""
from copy import deepcopy
import hashlib
import importlib.util
from pathlib import Path
import pytest

SCRIPT = Path(__file__).parents[2] / "src/cafe/data/skills/cafe-deliver_development/scripts/verify_github_actions.py"
spec = importlib.util.spec_from_file_location("github_delivery_tool", SCRIPT)
github_tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(github_tool)
COMMIT = "c" * 40
PATH = ".github/workflows/deploy.yml"


def inputs():
    return {"repository": "owner/repo", "target_branch": "develop", "commit": COMMIT,
            "options": {"workflows": [{"path": PATH, "jobs": {"deploy": ["Deploy", "Public checks"]}}]}}


def verification_plan(root=None):
    return {"scope": "Required CI, deploy and public endpoint checks",
            "tool": {"owner": "cafe-deliver_development", "path": "scripts/verify_github_actions.py",
                     "sha256": hashlib.sha256(SCRIPT.read_bytes()).hexdigest(),
                     "options": inputs()["options"]}}


def responses():
    return {
        "workflow_runs": [
            {
                "id": 123,
                "run_attempt": 1,
                "path": PATH,
                "head_sha": COMMIT,
                "head_branch": "develop",
                "event": "push",
                "repository": {"full_name": "owner/repo"},
                "status": "completed",
                "conclusion": "success",
            }
        ],
    }, {
        "total_count": 1,
        "jobs": [
            {
                "name": "deploy",
                "status": "completed",
                "conclusion": "success",
                "steps": [
                    {"name": "Deploy", "status": "completed", "conclusion": "success"},
                    {"name": "Public checks", "status": "completed", "conclusion": "success"},
                ],
            }
        ],
    }


def install(monkeypatch, runs=None, jobs=None):
    defaults = responses()
    runs = defaults[0] if runs is None else runs
    jobs = defaults[1] if jobs is None else jobs
    calls = []

    def api(self, endpoint):
        assert endpoint.startswith("repos/owner/repo/actions/")
        calls.append(endpoint)
        return deepcopy(jobs if "/jobs?" in endpoint else runs)

    monkeypatch.setattr(github_tool.Commands, "api", api)
    return calls


def test_exact_commit_success_and_required_deploy_public_steps(monkeypatch):
    calls = install(monkeypatch)
    current = github_tool.observe(inputs())
    assert current["state"] == "succeeded"
    assert current["commit"] == COMMIT and current["evidence"][0]["attempt"] == 1
    assert "head_sha=" + COMMIT in calls[0] and "branch=develop" in calls[0]

@pytest.mark.parametrize(
    "field,value",
    [
        ("head_sha", "d" * 40),
        ("event", "pull_request"),
        ("head_branch", "main"),
        ("path", ".github/workflows/other.yml"),
        ("repository", {"full_name": "other/repo"}),
    ],
)
def test_unrelated_workflow_is_unknown(monkeypatch, field, value):
    runs, jobs = responses()
    runs["workflow_runs"][0][field] = value
    install(monkeypatch, runs, jobs)
    assert github_tool.observe(inputs())["state"] == "unknown"


@pytest.mark.parametrize(
    "state,conclusion,expected",
    [
        ("queued", None, "pending"),
        ("in_progress", None, "pending"),
        ("completed", "failure", "failed"),
        ("completed", "cancelled", "failed"),
        ("completed", "skipped", "failed"),
        ("completed", "timed_out", "failed"),
    ],
)
def test_non_success_never_completes(monkeypatch, state, conclusion, expected):
    runs, jobs = responses()
    runs["workflow_runs"][0].update(status=state, conclusion=conclusion)
    install(monkeypatch, runs, jobs)
    assert github_tool.observe(inputs())["state"] == expected


@pytest.mark.parametrize(
    "change",
    [
        "missing_job",
        "duplicate_job",
        "skipped_job",
        "missing_public",
        "skipped_deploy",
        "failed_public",
        "truncated_jobs",
    ],
)
def test_successful_workflow_does_not_hide_unverified_obligations(monkeypatch, change):
    runs, jobs = responses()
    job = jobs["jobs"][0]
    if change == "missing_job":
        job["name"] = "other"
    if change == "duplicate_job":
        jobs["jobs"].append(deepcopy(job))
        jobs["total_count"] = 2
    if change == "skipped_job":
        job["conclusion"] = "skipped"
    if change == "missing_public":
        job["steps"].pop()
    if change == "skipped_deploy":
        job["steps"][0]["conclusion"] = "skipped"
    if change == "failed_public":
        job["steps"][1]["conclusion"] = "failure"
    if change == "truncated_jobs":
        jobs["total_count"] = 2
    install(monkeypatch, runs, jobs)
    assert github_tool.observe(inputs())["state"] in {"failed", "unknown"}


@pytest.mark.parametrize("suffix", ["", "@develop", "@refs/heads/develop"])
def test_documented_workflow_path_ref_representation(monkeypatch, suffix):
    runs, jobs = responses()
    runs["workflow_runs"][0]["path"] += suffix
    install(monkeypatch, runs, jobs)
    assert github_tool.observe(inputs())["state"] == "succeeded"


def test_different_workflow_path_ref_is_rejected(monkeypatch):
    runs, jobs = responses()
    runs["workflow_runs"][0]["path"] += "@main"
    install(monkeypatch, runs, jobs)
    assert github_tool.observe(inputs())["state"] == "unknown"



@pytest.mark.parametrize("message,retryable", [
    ("HTTP 401", False), ("HTTP 403: permission denied", False),
    ("HTTP 403: rate limit exceeded", True), ("HTTP 429", True),
    ("HTTP 503", True), ("connection timeout", True),
])
def test_transport_errors_are_classified(monkeypatch, message, retryable):
    from types import SimpleNamespace
    monkeypatch.setattr(github_tool.subprocess, "run", lambda *a, **kw:
                        SimpleNamespace(returncode=1, stderr=message, stdout=""))
    with pytest.raises(github_tool.ObservationError) as raised:
        github_tool.Commands(10).api("repos/owner/repo/actions/runs")
    assert raised.value.retryable is retryable


def test_missing_run_is_pending_not_a_human_failure(monkeypatch):
    install(monkeypatch, {"workflow_runs": []})
    assert github_tool.observe(inputs())["state"] == "pending"


def test_invalid_options_are_unknown(monkeypatch):
    request = inputs()
    request["options"]["workflows"][0]["path"] = "../bad.yml"
    monkeypatch.setattr(github_tool.Commands, "api", lambda *a: pytest.fail("API"))
    assert github_tool.observe(request)["state"] == "unknown"
