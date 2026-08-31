"""Strict version 2 workflow-driver policy tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cafe.core.driver_policy import DriverPolicyContract, extract_driver_policy


def _policy(mode: str = "attached") -> dict:
    driver: dict = {"mode": mode}
    if mode == "attached":
        driver["attached"] = {"poll_interval_seconds": 15}
    elif mode == "delegated":
        driver["delegated"] = {"cli": "codex", "availability": "required"}
    return {
        "contract_version": 2,
        "driver": driver,
        "execution": {"advancement": "continuous", "hosting": "foreground"},
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
        lambda value: value.pop("execution"),
        lambda value: value.update({"contract_version": 1}),
        lambda value: value.update({"driver_execution": "attached"}),
        lambda value: value["driver"].update({"delegated": {"cli": "codex", "availability": "required"}}),
        lambda value: value["driver"]["attached"].update({"unexpected": True}),
        lambda value: value["execution"].update({"unexpected": True}),
    ],
)
def test_incomplete_legacy_extra_and_mode_inapplicable_policy_fails_closed(mutate) -> None:
    value = _policy()
    mutate(value)

    with pytest.raises((ValidationError, ValueError)):
        extract_driver_policy(value)


def test_unattended_rejects_all_mode_specific_groups() -> None:
    value = _policy("unattended")
    value["driver"]["attached"] = {"poll_interval_seconds": 10}

    with pytest.raises(ValidationError):
        extract_driver_policy(value)

