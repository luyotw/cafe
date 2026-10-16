"""Preparation-time delivery choices win over agent drafts on real host paths."""

import json

import pytest
import yaml
from typer.testing import CliRunner

from cafe.delivery.selection import proposal_path
from cafe.ui.cli import app
from tests.integration import test_development_delivery_journey as journey
from tests.integration.test_development_delivery_journey import (
    approve_action,
    hook,
    setup_action,
)

local_action = journey.local_action
fake_github = journey.fake_github


@pytest.mark.parametrize("github", [False, True])
def test_prepared_endpoint_overrides_agent_draft(local_action, fake_github, tmp_path, github):
    context = setup_action(local_action, tmp_path, github=github, prepare_review=False)
    root, dest, issue, state, phase, data, engine, kwargs, _, step = context
    policy = json.loads((kwargs["output_file"].parent / "delivery_request.json").read_text())
    policy.pop("issue_repository")
    (issue / "issue.yaml").write_text(
        yaml.safe_dump({"playbook_id": data["playbook"]["id"], step: policy})
    )
    # An agent's conflicting local/main proposal is ignored in favor of fixed choices.
    (kwargs["output_file"].parent / "delivery_request.json").write_text(
        json.dumps(
            {
                "mode": "local",
                "target_branch": "main",
                "strategy": "merge-commit",
                "destination": str(dest),
                "verification": {"not_required_reason": "Wrong draft"},
            }
        )
    )
    result = hook(engine, "publish_output", "DevelopmentActionContext", kwargs)
    assert result.continue_pipeline, result.context_updates
    from cafe.core.human_task_records import HumanTaskRecordStore

    task = HumanTaskRecordStore(issue).get_task(
        result.context_updates["delivery_action_review_task"]
    )
    proposal = json.loads(proposal_path(issue, task.id).read_text())
    assert proposal["mode"] == ("github" if github else "local")
    assert proposal["target_branch"] == "develop"
    assert proposal["verification"]["tool"] is None
    assert (
        proposal["verification"]["not_required_reason"]
        == policy["verification"]["not_required_reason"]
    )


def test_delivery_cli_runs_approved_local_bundle_and_reconciles_without_merge_replay(
    local_action,
    tmp_path,
    monkeypatch,
):
    context = setup_action(local_action, tmp_path)
    root, dest, issue, state, phase, data, engine, kwargs, task, step = context
    request = json.loads((kwargs["output_file"].parent / "delivery_request.json").read_text())
    request.pop("issue_repository")
    (issue / "issue.yaml").write_text(
        yaml.safe_dump({"playbook_id": data["playbook"]["id"], step: request})
    )
    approve_action(context)
    monkeypatch.chdir(root)
    first = CliRunner().invoke(app, ["delivery", "run", "--issue", issue.name, "--step", step])
    assert first.exit_code == 0, first.output + repr(first.exception)
    report = json.loads(first.output)["result"]
    assert report["complete"] is True
    assert (dest / "result.txt").read_text() == "Reviewed change"
    from cafe.delivery.operations import Commands

    original = Commands.run

    def no_mutation(self, argv, **kw):
        assert "merge" not in argv
        return original(self, argv, **kw)

    monkeypatch.setattr(Commands, "run", no_mutation)
    again = CliRunner().invoke(app, ["delivery", "run", "--issue", issue.name, "--step", step])
    assert again.exit_code == 0, again.output
    assert (
        json.loads(again.output)["result"]["actions"]["integration"]["commit"]
        == report["actions"]["integration"]["commit"]
    )


def test_invalid_prepared_policy_is_not_replaced_by_agent_request(local_action, tmp_path):
    context = setup_action(local_action, tmp_path, prepare_review=False)
    root, dest, issue, state, phase, data, engine, kwargs, _, step = context
    (issue / "issue.yaml").write_text(yaml.safe_dump({step: {"mode": "github"}}))
    result = hook(engine, "publish_output", "DevelopmentActionContext", kwargs)
    assert not result.continue_pipeline
    assert "validation" in result.context_updates["delivery_action_error"].lower()


@pytest.mark.parametrize("valid", [False, True])
def test_public_prepare_validates_delivery_policy_before_branch_creation(
    local_action, monkeypatch, valid
):
    root, dest, _, snapshot = local_action
    from tests.conftest import create_minimal_config

    create_minimal_config(root)
    monkeypatch.chdir(root)
    monkeypatch.setattr("cafe.ui.commands.lifecycle._offer_cli_update", lambda **kw: None)
    args = [
        "prepare",
        "prepared",
        "--playbook",
        "direct",
        "--no-interactive",
        "--no-check",
        "--input-method",
        "manual",
        "--no-auto-create-pr",
        "--set",
        "deliver.mode=local",
        "--set",
        "deliver.strategy=ff-only",
        "--set",
        "deliver.target_branch=develop",
        "--set",
        f"deliver.destination={dest}",
        "--set",
        (
            'deliver.verification={"not_required_reason":"Explicit offline integration scope"}'
            if valid
            else 'deliver.verification={"scope":"Missing observer and no explicit requirement"}'
        ),
    ]
    result = CliRunner().invoke(app, args)
    from tests.integration.test_development_delivery_operations import git

    if valid:
        assert result.exit_code == 0, result.output + repr(result.exception)
        config = yaml.safe_load((root / ".cafe/issues/prepared/issue.yaml").read_text())
        assert config["deliver"]["mode"] == "local"
        assert config["deliver"]["target_branch"] == "develop"
    else:
        assert result.exit_code != 0
        assert git(root, "branch", "--show-current") == "feature"
        assert not (root / ".cafe/issues/prepared/issue.yaml").exists()


def test_delivery_cli_rejects_superseded_publication_before_dispatch(
    local_action, tmp_path, monkeypatch
):
    context = setup_action(local_action, tmp_path)
    root, dest, issue, state, phase, data, engine, kwargs, task, step = context
    (issue / "issue.yaml").write_text(yaml.safe_dump({"playbook_id": data["playbook"]["id"]}))
    approve_action(context)
    from cafe.core.blackboard import BlackboardStore

    store = BlackboardStore(issue)
    latest = store.load_or_create(step)
    name = data["steps"][step]["delivery"]["publication_artifact"]
    replacement = kwargs["output_file"].with_name("revised-publication.md")
    replacement.write_text("# Revised publication\n")
    latest.artifacts[name].path = str(replacement)
    store.save(latest)
    monkeypatch.chdir(root)
    result = CliRunner().invoke(app, ["delivery", "run", "--issue", issue.name, "--step", step])
    assert result.exit_code == 1
    assert "publication changed" in result.output
    assert not (dest / "result.txt").exists()


def test_prepared_policy_preserves_selected_issue_destination(local_action, tmp_path):
    context = setup_action(
        local_action,
        tmp_path,
        prepare_review=False,
        proposals=[{"id": "FUP-001", "title": "Fix", "body": "Fix later", "evidence": "path:1"}],
    )
    root, dest, issue, state, phase, data, engine, kwargs, _, step = context
    request = json.loads((kwargs["output_file"].parent / "delivery_request.json").read_text())
    (issue / "issue.yaml").write_text(yaml.safe_dump({step: request}))
    result = hook(engine, "publish_output", "DevelopmentActionContext", kwargs)
    assert result.continue_pipeline, result.context_updates
    proposal = json.loads(
        proposal_path(issue, result.context_updates["delivery_action_review_task"]).read_text()
    )
    assert proposal["issue_repository"] == "owner/repo"
    assert "FUP-001" in proposal["capability_review"]


def test_automatic_delivery_requires_choices_before_creating_branch(local_action, monkeypatch):
    root, dest, _, snapshot = local_action
    from tests.conftest import create_minimal_config
    from tests.integration.test_development_delivery_operations import git
    create_minimal_config(root)
    monkeypatch.chdir(root)
    monkeypatch.setattr("cafe.ui.commands.lifecycle._offer_cli_update", lambda **kw: None)
    result = CliRunner().invoke(app, ["prepare", "missing-endpoint", "--playbook", "direct",
        "--no-interactive", "--no-check", "--input-method", "manual", "--no-auto-create-pr"])
    assert result.exit_code != 0
    assert "automatic delivery requires prepare choices" in result.output
    assert git(root, "branch", "--show-current") == "feature"
    assert not (root / ".cafe/issues/missing-endpoint/issue.yaml").exists()
