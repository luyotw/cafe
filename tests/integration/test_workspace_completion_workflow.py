"""Workspace completion journeys through the public workflow runtime (I1–I8)."""
import json
import subprocess

import pytest

from cafe.core.human_task_records import HumanTaskRecordStore
from tests.integration.test_workflow_artifact_correction import CORRECTED, journey


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True).stdout


def change_then_commit(repo, iteration, call):
    if call == 1:
        (repo / "owned.txt").write_text("authorized change\n")
    else:
        git(repo, "add", "owned.txt")
        git(repo, "commit", "-m", "authorized change")


@pytest.mark.xfail(reason="Completion callback implemented by PLAN-006", strict=True)
@pytest.mark.parametrize("mode", ["baton", "legacy"])
def test_i1_custom_step_corrects_before_effects_in_exact_context(journey, mode):
    j = journey([CORRECTED], mode=mode, workspace=True, workspace_action=change_then_commit)
    j.runtime.run(start_step="inspect_custom")
    assert len(j.manager.calls) == 2
    first, correction = j.manager.calls
    assert correction[2].is_exact
    assert correction[2].session_id == "exact-report-session"
    assert correction[3]["allowed_tools"] == first[3]["allowed_tools"]
    assert correction[3]["allowed_directories"] == first[3]["allowed_directories"]
    assert j.effects == ["prepare", "after"]
    assert not HumanTaskRecordStore(j.issue).tasks()
    assert "custom_snapshot" in j.runtime.blackboard.artifacts
    assert j.manager.deliveries == 1


@pytest.mark.xfail(reason="Human precedence implemented by PLAN-006", strict=True)
@pytest.mark.parametrize("intent", ["need_clarification", "need_permission"])
@pytest.mark.parametrize("initial", [True, False])
def test_i4_preserves_preexisting_changes_and_human_handoff(journey, intent, initial):
    observations = []
    def preexisting(repo, iteration, call):
        if call == 1:
            for index in range(6):
                (repo / f"template-{index}.txt").write_text(f"owner {index}")
        observations.append((git(repo, "rev-parse", "HEAD"), git(repo, "diff", "--cached"),
                             git(repo, "status", "--porcelain=v1")))
    j = journey([(CORRECTED, intent)] if initial else [CORRECTED, (CORRECTED, intent)],
                workspace=True, workspace_action=preexisting, human=intent)
    j.runtime.run(start_step="inspect_custom")
    assert len(j.manager.calls) == (1 if initial else 2)
    assert j.manager.deliveries == 0
    assert j.runtime.blackboard.handoff_contract.to_owner.value == "user"
    assert j.runtime.blackboard.handoff_contract.intent.value == intent
    assert "custom_snapshot" not in j.runtime.blackboard.artifacts
    assert j.effects == ["prepare"]
    assert observations[-1] == observations[0]
    assert git(j.repo, "status", "--porcelain=v1") == observations[0][2]


def test_i7_undeclared_step_keeps_dirty_workspace_behavior(journey):
    def dirty(repo, iteration, call):
        (repo / "owned.txt").write_text("dirty")
    j = journey([CORRECTED], workspace=False, workspace_action=dirty)
    j.runtime.run(start_step="inspect_custom")
    assert len(j.manager.calls) == 1 and j.manager.deliveries == 1
    assert j.effects == ["prepare", "after"]
    assert "custom_snapshot" not in j.runtime.blackboard.artifacts
