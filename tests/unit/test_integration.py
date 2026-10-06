"""Post-review selection and evidence invariants (U1, U2, U4, U5)."""

import pytest

from cafe.core.integration import IntegrationSelection, accepted_review
from cafe.core.playbook import IntegrationDeclaration


@pytest.fixture
def declaration():
    return IntegrationDeclaration(
        review_step="judgement",
        review_task="accept-delivery",
        accepted_decisions=["ship"],
        source_artifact="reviewed-tree",
        delivery_artifact="proposal",
        selection_step="destination",
        selection_task="choose",
    )


def selection(**overrides):
    values = dict(
        target="local_branch",
        repository="/tmp/reviewed",
        source_commit="a" * 40,
        feature_branch="feature",
        target_branch="main",
    )
    return IntegrationSelection(**(values | overrides))


@pytest.mark.parametrize(
    "changes",
    [
        {"repository": ""},
        {"source_commit": "HEAD"},
        {"target_branch": ""},
        {"target_branch": "feature"},
        {"target_branch": "refs/remotes/origin/main"},
        {"target": "script"},
        {"target_branch": "--help"},
    ],
)
def test_selection_requires_exact_explicit_identity(changes):
    with pytest.raises(ValueError):
        selection(**changes)


def test_github_selection_requires_pr_and_repository_identity():
    with pytest.raises(ValueError):
        selection(target="github_pr", repository="owner/repo")
    assert selection(target="github_pr", repository="owner/repo", pr=17).pr == 17


def test_review_binding_uses_declared_task_decision_and_source(declaration):
    source = dict(repository="/tmp/reviewed", head_sha="a" * 40, version=3)
    review = accepted_review(
        declaration,
        task_id="task-1",
        result_id="result-1",
        step="judgement",
        policy_id="accept-delivery",
        decision="ship",
        source=source,
        source_identity="b" * 64,
    )
    assert review.source_commit == source["head_sha"]
    assert review.result_id == "result-1"
    for change in [{"decision": "fix"}, {"step": "review"}, {"policy_id": "local-review"}]:
        args = (
            dict(
                task_id="task-1",
                result_id="result-1",
                step="judgement",
                policy_id="accept-delivery",
                decision="ship",
                source=source,
                source_identity="b" * 64,
            )
            | change
        )
        with pytest.raises(ValueError):
            accepted_review(declaration, **args)


# U4: supported GitHub merge methods qualify through exact PR evidence.
def github_observation(**changes):
    return (
        dict(
            repository="owner/repo",
            pr=17,
            source_commit="a" * 40,
            target_branch="main",
            merged=True,
            merge_commit="c" * 40,
            state="closed",
        )
        | changes
    )


@pytest.mark.parametrize("method", ["merge", "squash", "rebase"])
def test_exact_approved_github_pr_qualifies_without_local_ancestry(method):
    from cafe.core.integration import evaluate_github

    selected = selection(target="github_pr", repository="owner/repo", pr=17)
    assert evaluate_github(selected, github_observation(merge_method=method))[0]


@pytest.mark.parametrize(
    "changes",
    [
        {"repository": "other/repo"},
        {"pr": 18},
        {"source_commit": "b" * 40},
        {"target_branch": "other"},
        {"merged": False, "state": "open"},
        {"merged": False},
        {"merge_commit": None},
        {"merge_commit": "claimed"},
        {"merged": "true"},
        {"unavailable": True},
    ],
)
def test_github_wrong_missing_or_unavailable_proof_is_incomplete(changes):
    from cafe.core.integration import evaluate_github

    selected = selection(target="github_pr", repository="owner/repo", pr=17)
    success, reason = evaluate_github(selected, github_observation(**changes))
    assert not success and reason


def test_boolean_pr_is_not_an_explicit_pr_identity():
    with pytest.raises(ValueError):
        selection(target="github_pr", repository="owner/repo", pr=True)


def test_local_exit_status_is_integer_evidence_not_truthy_claim():
    from cafe.core.integration import evaluate_local

    selected = selection()
    observed = dict(
        repository="/tmp/reviewed",
        target_branch="main",
        source_commit="a" * 40,
        target_head="a" * 40,
        stable=True,
        ancestor_exit_code=False,
    )
    assert not evaluate_local(selected, observed)[0]
