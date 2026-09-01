"""Strict version 2 workflow-driver policy tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cafe.core.driver_policy import DriverPolicyContract, extract_driver_policy
from cafe.ui.commands.workflow import _load_driver_policy_for_execution


def _policy(mode: str = "attached") -> dict:
    driver: dict = {"mode": mode}
    if mode == "attached":
        driver["poll_interval_seconds"] = 15
    elif mode == "delegated":
        driver.update({"cli": "codex", "model": "gpt-5.6-codex"})
    return {
        "contract_version": 2,
        "driver": driver,
    }


@pytest.mark.parametrize("mode", ["attached", "unattended", "delegated"])
def test_complete_policy_accepts_each_owner_without_losing_issue_metadata(mode: str) -> None:
    issue = {
        "base_branch": "develop",
        "feature_branch": "issue432",
        "playbook_id": "custom",
        "review": {"agent": "Grace", "future": {"enabled": True}},
        **_policy(mode),
    }

    policy = extract_driver_policy(issue)

    assert isinstance(policy, DriverPolicyContract)
    assert policy.driver.mode == mode
    assert issue["review"]["future"] == {"enabled": True}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.pop("contract_version"),
        lambda value: value.update({"contract_version": 1}),
        lambda value: value.update({"driver_execution": "attached"}),
        lambda value: value.update(
            {"execution": {"advancement": "continuous", "hosting": "foreground"}}
        ),
        lambda value: value["driver"].update(
            {"delegated": {"cli": "codex", "model": "gpt-5.6-codex"}}
        ),
        lambda value: value["driver"].update({"availability": "required"}),
        lambda value: value["driver"].update({"hosting": "background"}),
        lambda value: value["driver"].update({"advancement": "single_step"}),
        lambda value: value["driver"].update({"unexpected": True}),
    ],
)
def test_incomplete_legacy_extra_and_mode_inapplicable_policy_fails_closed(mutate) -> None:
    value = _policy()
    mutate(value)

    with pytest.raises((ValidationError, ValueError)):
        extract_driver_policy(value)


def test_unattended_rejects_all_mode_specific_groups() -> None:
    value = _policy("unattended")
    value["driver"]["poll_interval_seconds"] = 10

    with pytest.raises(ValidationError):
        extract_driver_policy(value)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["driver"].pop("model"),
        lambda value: value["driver"].update({"model": ""}),
        lambda value: value["driver"].update({"poll_interval_seconds": 10}),
    ],
)
def test_delegated_requires_one_supported_cli_and_exact_model(mutate) -> None:
    value = _policy("delegated")
    mutate(value)

    with pytest.raises(ValidationError):
        extract_driver_policy(value)


def test_public_execution_validation_rejects_absent_policy_without_runtime_mutation(
    tmp_path,
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "issue432"
    issue_dir.mkdir(parents=True)

    with pytest.raises(ValidationError):
        _load_driver_policy_for_execution(issue_dir)

    assert not (issue_dir / "blackboard.json").exists()
