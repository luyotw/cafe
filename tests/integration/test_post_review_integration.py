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
