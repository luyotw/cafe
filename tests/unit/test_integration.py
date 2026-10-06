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
        source_step="build",
        delivery_artifact="proposal",
        delivery_step="package",
        selection_step="destination",
        selection_task="choose",
        action_step="delivery",
        action_task="human-delivery",
        correction_step="build",
        verified_continuation="_done",
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
