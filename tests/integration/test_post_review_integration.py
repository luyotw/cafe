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
