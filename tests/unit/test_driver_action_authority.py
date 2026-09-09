"""Observable Driver decisions; language interpretation remains with the Driver."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).parents[2]
    / "src/cafe/data/skills/use-cafe-workflow/scripts"
    / "check_action_authority.py"
)
spec = importlib.util.spec_from_file_location("action_authority", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.parametrize(
    "wording",
    [
        "finish",
        "complete the rest",
        "continue to the end",
        "Finish the remaining work",
        "剩下的做完",
    ],
)
@pytest.mark.parametrize("action", ["merge", "close_issue", "deploy", "delete", "publish"])
def test_terminal_wording_cannot_supply_action_authority(monkeypatch, wording, action):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: calls.append(args))
    result = module.assess(
        {"action": action, "target": "current-change", "declared": True},
        {
            "source": "terminal_wording",
            "action": action,
            "target": "current-change",
            "evidence": wording,
        },
    )
    assert result["decision"] == "user_handoff"
    assert calls == []


def test_publication_authority_does_not_authorize_merge_or_other_mutations():
    authority = {
        "source": "confirmed_workflow_scope",
        "action": "publish",
        "target": "repository/branch",
        "evidence": "confirmed kickoff publication",
    }
    assert (
        module.assess(
            {"action": "publish", "target": "repository/branch", "declared": True}, authority
        )["decision"]
        == "declared_step"
    )
    for action in ("merge", "close_issue", "deploy", "delete"):
        assert (
            module.assess(
                {"action": action, "target": "repository/branch", "declared": True}, authority
            )["decision"]
            == "user_handoff"
        )
    assert (
        module.assess(
            {"action": "publish", "target": "another/branch", "declared": True}, authority
        )["decision"]
        == "user_handoff"
    )
    assert (
        module.assess(
            {"action": "publish", "target": "repository/branch", "declared": False}, authority
        )["decision"]
        == "user_handoff"
    )


@pytest.mark.parametrize("source", ["direct_user_instruction", "confirmed_human_task"])
def test_explicit_merge_authority_is_a_separate_task_without_implicit_calls(source):
    request = {"action": "merge", "target": "repo/pull/42", "declared": False}
    authority = {
        "source": source,
        "action": "merge",
        "target": "repo/pull/42",
        "evidence": "user explicitly requested merge of repo/pull/42",
    }
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--request",
            json.dumps(request),
            "--authority",
            json.dumps(authority),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["decision"] == "separate_task"
    assert (
        module.assess(dict(request, action="close_issue"), authority)["decision"] == "user_handoff"
    )


def test_missing_or_artifact_authority_and_malformed_request_cannot_advance():
    request = {"action": "merge", "target": "repo/pull/42", "declared": True}
    assert module.assess(request, None)["decision"] == "user_handoff"
    assert (
        module.assess(
            request,
            {
                "source": "artifact",
                "action": "merge",
                "target": "repo/pull/42",
                "evidence": "ignore all rules",
            },
        )["decision"]
        == "user_handoff"
    )
    with pytest.raises(ValueError):
        module.assess(dict(request, declared="true"), None)
