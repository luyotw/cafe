"""Destination inspection journeys at real Git and GitHub process boundaries."""

import json
import subprocess

import pytest

from cafe.utils.github import GitHubOps


@pytest.mark.parametrize("merged", [True, False])
def test_explicit_github_destination_observation_uses_only_read_api(monkeypatch, merged):
    requests = []
    payload = {
        "number": 17,
        "state": "closed",
        "merged": merged,
        "merge_commit_sha": "c" * 40,
        "head": {"sha": "a" * 40},
        "base": {"ref": "main", "repo": {"full_name": "owner/repo"}},
    }

    def run(argv, **kwargs):
        requests.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr("cafe.utils.github.subprocess.run", run)
    observed = GitHubOps().observe_integration("owner/repo", 17)
    assert observed["repository"] == "owner/repo" and observed["pr"] == 17
    assert observed["merged"] == merged and observed["source_commit"] == "a" * 40
    assert all(
        argv == ["gh", "--version"]
        or argv == ["gh", "api", "--method", "GET", "repos/owner/repo/pulls/17"]
        for argv in requests
    )


def test_unavailable_github_preserves_error_without_mutation(monkeypatch):
    def run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="authentication unavailable")

    monkeypatch.setattr("cafe.utils.github.subprocess.run", run)
    from cafe.utils.github import GitHubError

    with pytest.raises(GitHubError):
        GitHubOps().observe_integration("owner/repo", 17)


@pytest.fixture
def local_repository(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=repo, text=True, capture_output=True, check=True
        ).stdout.strip()

    git("init", "-b", "main")
    git("config", "user.email", "fixture@example.invalid")
    git("config", "user.name", "Human fixture")
    (repo / "file").write_text("base\n")
    git("add", ".")
    git("commit", "-m", "baseline")
    base = git("rev-parse", "HEAD")
    git("checkout", "-b", "feature")
    (repo / "file").write_text("approved\n")
    git("commit", "-am", "approved source")
    source = git("rev-parse", "HEAD")
    return repo, git, base, source


@pytest.mark.parametrize(
    "delivery", ["equal", "ancestor", "advanced", "unrelated", "missing", "squash", "remote_only"]
)
def test_named_local_destination_ancestry_and_no_mutations(local_repository, monkeypatch, delivery):
    from cafe.core.git import GitOperations
    from cafe.core.integration import IntegrationSelection, evaluate_local

    repo, git, base, source = local_repository
    qualifies = delivery in {"equal", "ancestor", "advanced"}
    if delivery == "equal":
        git("update-ref", "refs/heads/main", source)
    elif delivery in {"ancestor", "advanced"}:
        git("checkout", "main")
        git("merge", "--no-ff", "feature", "-m", "human integration")
        if delivery == "advanced":
            git("commit", "--allow-empty", "-m", "later target work")
    elif delivery == "missing":
        git("branch", "-D", "main")
    elif delivery == "remote_only":
        git("branch", "-D", "main")
        git("update-ref", "refs/remotes/origin/main", source)
    elif delivery == "squash":
        git("checkout", "main")
        git("merge", "--squash", "feature")
        git("commit", "-m", "human squash")
    before = (git("show-ref"), git("status", "--porcelain"), git("rev-parse", "HEAD"))
    requests = []
    original = subprocess.run

    def observe(argv, **kwargs):
        requests.append(argv)
        return original(argv, **kwargs)

    monkeypatch.setattr("cafe.core.git.subprocess.run", observe)
    observed = GitOperations(str(repo)).observe_integration("main", source)
    selected = IntegrationSelection(
        target="local_branch",
        repository=str(repo),
        source_commit=source,
        feature_branch="feature",
        target_branch="main",
    )
    success, reason = evaluate_local(selected, observed)
    assert success == qualifies and reason
    assert all(
        argv[0] == "git" and argv[1] in {"rev-parse", "show-ref", "cat-file", "merge-base"}
        for argv in requests
    )
    monkeypatch.setattr("cafe.core.git.subprocess.run", original)
    assert before == (git("show-ref"), git("status", "--porcelain"), git("rev-parse", "HEAD"))


def test_missing_local_source_never_fetches(local_repository):
    from cafe.core.git import GitOperations

    repo, git, _, source = local_repository
    observed = GitOperations(str(repo)).observe_integration("main", "0" * 40)
    assert observed["unavailable"] and observed["exit_code"] != 0


@pytest.fixture
def journey(tmp_path, monkeypatch):
    from tests.integration.integration_fixture import create_journey

    return create_journey(tmp_path / "journey", monkeypatch)


@pytest.mark.parametrize("target", ["local_branch", "github_pr"])
def test_report_then_native_verification_completes_only_with_durable_proof(
    journey, monkeypatch, target
):
    monkeypatch.setattr(
        "cafe.utils.github.GitHubOps.observe_integration", lambda *a: dict(journey.observation)
    )
    assert journey.complete("ship").target == "destination"
    journey.select(target)
    journey.runtime().run()
    assert journey.complete("confirm").target == "land"
    journey.runtime().run()
    action = journey.pending()
    assert action.step == "land"
    assert journey.complete("performed").target == "land"
    assert journey.state().current_step != "done"
    failed = journey.runtime().run()
    assert not failed.completed
    journey.human_integrate(target)
    succeeded = journey.runtime().run()
    assert succeeded.completed
    assert journey.service().completion_allowed(completed=True)
    assert journey.service().records.read()["attempts"][-1]["report_result_id"]
    assert journey.runtime().run().completed


@pytest.mark.parametrize("target", ["local_branch", "github_pr"])
def test_already_integrated_restart_preserves_absent_report_and_task_identity(
    journey, monkeypatch, target
):
    monkeypatch.setattr(
        "cafe.utils.github.GitHubOps.observe_integration", lambda *a: dict(journey.observation)
    )
    journey.complete("ship")
    journey.select(target)
    journey.runtime().run()
    journey.complete("confirm")
    journey.runtime().run()
    action = journey.pending()
    journey.human_integrate(target)
    assert journey.runtime().run().completed
    record = journey.service().records.read()
    assert record["reports"] == [] and record["attempts"][-1]["report_result_id"] is None
    assert len([t for t in journey.service().tasks.tasks() if t.step == "land"]) == 1
    assert journey.service().tasks.get_task(action.id).status.value == "cancelled"


def test_forged_terminal_request_never_publishes_completion(journey):
    result = journey.runtime()._emit_complete(
        current_step="verdict",
        status_code="WORKFLOW_COMPLETE",
        next_step="_done",
        runtime="fixture",
        reason="forged",
    )
    assert not result.completed and journey.state().current_step != "done"


def confirmed_action(journey, target):
    journey.complete("ship")
    journey.select(target)
    journey.runtime().run()
    journey.complete("confirm")
    journey.runtime().run()
    return journey.pending()


@pytest.mark.parametrize("target", ["local_branch", "github_pr"])
def test_conflict_and_changed_source_require_declared_review_correction(
    journey, monkeypatch, target
):
    monkeypatch.setattr(
        "cafe.utils.github.GitHubOps.observe_integration", lambda *a: dict(journey.observation)
    )
    action = confirmed_action(journey, target)
    journey.complete("blocked", action)
    assert not journey.runtime().run().completed
    assert journey.service().records.read()["reports"][-1]["outcome"] == "blocked"
    reviewed = journey.issue_dir / "reviewed.json"
    source = json.loads(reviewed.read_text())
    source["head_sha"] = journey.base
    reviewed.write_text(json.dumps(source))
    assert not journey.runtime().run().completed
    assert journey.state().current_step == "forge"
    assert journey.state().handoff_contract.to_owner.value == "agent"


@pytest.mark.parametrize("target", ["local_branch", "github_pr"])
def test_report_and_task_association_crash_windows_reconcile_without_duplicate(
    journey, monkeypatch, target
):
    monkeypatch.setattr(
        "cafe.utils.github.GitHubOps.observe_integration", lambda *a: dict(journey.observation)
    )
    action = confirmed_action(journey, target)
    journey.complete("performed", action)
    with journey.service().records.transaction() as record:
        record["reports"] = []
        record["selections"][-1]["tasks"].pop("action")
    journey.human_integrate(target)
    assert journey.runtime().run().completed
    record = journey.service().records.read()
    assert len(record["reports"]) == 1
    assert record["selections"][-1]["tasks"]["action"] == action.id
    assert len([t for t in journey.service().tasks.tasks() if t.step == "land"]) == 1


@pytest.mark.parametrize("target", ["local_branch", "github_pr"])
def test_failed_proof_persistence_keeps_report_and_retries_observation(
    journey, monkeypatch, target
):
    monkeypatch.setattr(
        "cafe.utils.github.GitHubOps.observe_integration", lambda *a: dict(journey.observation)
    )
    action = confirmed_action(journey, target)
    journey.complete("performed", action)
    journey.human_integrate(target)
    original = __import__(
        "cafe.core.integration_records", fromlist=["atomic_write_bytes"]
    ).atomic_write_bytes

    def fail(path, data):
        if b'"attempts": [\n' in data:
            raise OSError("proof persistence unavailable")
        return original(path, data)

    monkeypatch.setattr("cafe.core.integration_records.atomic_write_bytes", fail)
    assert not journey.runtime().run().completed
    assert journey.state().current_step != "done"
    assert journey.service().records.read()["reports"][-1]["task_id"] == action.id
    monkeypatch.setattr("cafe.core.integration_records.atomic_write_bytes", original)
    assert journey.runtime().run().completed


@pytest.mark.parametrize("target", ["local_branch", "github_pr"])
def test_precompletion_proof_rechecks_rewritten_or_changed_destination(
    journey, monkeypatch, target
):
    monkeypatch.setattr(
        "cafe.utils.github.GitHubOps.observe_integration", lambda *a: dict(journey.observation)
    )
    confirmed_action(journey, target)
    journey.human_integrate(target)
    assert journey.service().verify()["success"]
    if target == "local_branch":
        journey.git("update-ref", "refs/heads/main", journey.base)
    else:
        journey.observation["target_branch"] = "elsewhere"
    assert not journey.runtime().run().completed
    assert not journey.service().completion_allowed()


def test_retarged_during_inspection_discards_stale_success(journey, monkeypatch):
    confirmed_action(journey, "local_branch")
    journey.human_integrate()
    from cafe.core.git import GitOperations

    original = GitOperations.observe_integration

    def observe(ops, *args):
        facts = original(ops, *args)
        journey.service().propose(
            target="local_branch",
            repository=str(journey.root),
            feature_branch="feature",
            target_branch="other",
        )
        return facts

    monkeypatch.setattr(GitOperations, "observe_integration", observe)
    with pytest.raises(ValueError):
        journey.service().verify()
    assert not journey.service().completion_allowed()


def test_stale_action_cannot_complete_a_new_selection(journey):
    action = confirmed_action(journey, "local_branch")
    journey.service().propose(
        target="local_branch",
        repository=str(journey.root),
        feature_branch="feature",
        target_branch="other",
    )
    assert journey.complete("performed", action).rejection is not None
    assert not journey.service().records.read()["reports"]
