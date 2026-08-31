"""Behavior-contract tests for arbitrary playbook step names."""

import ast
import inspect

import pytest

from cafe.agents import manager as agent_manager
from cafe.core import workflow_runtime
from cafe.core.hooks import native as native_hooks
from cafe.core.hooks.native import _publish_requested
from cafe.core.playbook import PlaybookDefinition, resolve_step_behavior
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
from cafe.phases import generic_phase, generic_workflow_step
from cafe.playbooks.loader import PlaybookLoader
from cafe.ui import cli_shared


def test_agent_manager_has_no_fixed_read_only_retry_policy():
    """UT-007: investigation and monitoring have no forced-edit budget."""
    assert not hasattr(agent_manager.AgentManager, "READ_ONLY_RETRY_LIMIT")
    assert not hasattr(agent_manager.AgentManager, "READ_ONLY_RETRY_PROMPT")


def _playbook(*, build_behavior=None, defaults=None):
    payload = {
        "playbook": {"id": "custom"},
        "steps": {
            "build": {
                "role": "operator",
                "skill": "phase",
                "on": {"await_agent": "verify"},
            },
            "verify": {
                "role": "operator",
                "skill": "phase",
                "input_artifacts": ["workflow_feedback"],
                "on": {"await_agent": "_done"},
            },
        },
    }
    if defaults is not None:
        payload["behavior"] = defaults
    if build_behavior is not None:
        payload["steps"]["build"]["behavior"] = build_behavior
        if build_behavior.get("publish_confirmation"):
            payload["steps"]["build"]["capability_requests"] = ["cafe.pr.publish"]
    return payload


def test_behavior_contract_merges_defaults_for_arbitrary_step_names():
    """UT-002: step declarations override stable playbook-wide defaults."""
    model = PlaybookDefinition.model_validate(
        _playbook(
            defaults={
                "completion": "status_code",
                "context_providers": ["workflow_metadata"],
                "runtime_tool_grants": ["web_research"],
            },
            build_behavior={
                "completion": "baton",
                "publish_confirmation": True,
                "feedback_target": "verify",
                "runtime_tool_grants": ["git_inspection"],
            },
        )
    )

    behavior = resolve_step_behavior(model, "build")

    assert behavior.completion == "baton"
    assert behavior.publish_confirmation is True
    assert behavior.feedback_target == "verify"
    assert behavior.context_providers == ["workflow_metadata"]
    assert behavior.runtime_tool_grants == ["git_inspection"]


def test_behavior_contract_step_values_override_playbook_defaults():
    """UT-002/UT-006/UT-007: a step can narrow inherited runtime access."""
    model = PlaybookDefinition.model_validate(
        _playbook(
            defaults={
                "context_providers": ["workflow_metadata"],
                "runtime_tool_grants": ["web_research"],
            },
            build_behavior={
                "context_providers": ["git_history"],
                "runtime_tool_grants": ["git_inspection"],
            },
        )
    )

    behavior = resolve_step_behavior(model, "build")

    assert behavior.context_providers == ["git_history"]
    assert behavior.runtime_tool_grants == ["git_inspection"]


@pytest.mark.parametrize("playbook_id", ["tdd", "hotfix"])
def test_bundled_review_steps_preserve_declared_runtime_review_grants(playbook_id):
    """UT-007: bundled review steps retain their declared inspection capabilities."""
    playbook = PlaybookLoader().load_model(playbook_id).model

    assert resolve_step_behavior(playbook, "review").runtime_tool_grants == [
        "web_research",
        "git_inspection",
    ]


@pytest.mark.parametrize("step_name", ["assemble", "quality_gate"])
def test_omitted_behavior_uses_same_universal_defaults_for_any_step_name(step_name):
    """UT-002: omission never recovers semantics from reserved phase names."""
    model = PlaybookDefinition.model_validate(
        {
            "playbook": {"id": "arbitrary"},
            "steps": {
                step_name: {"role": "operator", "skill": "phase", "on": {"await_agent": "_done"}}
            },
        }
    )

    behavior = resolve_step_behavior(model, step_name)

    assert behavior.completion == "status_code"
    assert behavior.publish_confirmation is False
    assert behavior.feedback_target is None
    assert behavior.context_providers == []
    assert behavior.runtime_tool_grants == []


def test_behavior_contract_rejects_removed_read_only_budget():
    """UT-007: public playbook declarations cannot reintroduce a global edit guard."""
    with pytest.raises(ValueError, match="max_read_only_commands"):
        PlaybookDefinition.model_validate(
            _playbook(build_behavior={"max_read_only_commands": 20})
        )


def test_behavior_contract_rejects_unknown_grant_and_unknown_feedback_target():
    """UT-001: declarations fail closed before workflow execution."""
    with pytest.raises(ValueError, match="runtime_tool_grants"):
        PlaybookDefinition.model_validate(
            _playbook(build_behavior={"runtime_tool_grants": ["untrusted_shell"]})
        )

    with pytest.raises(ValueError, match="feedback_target"):
        PlaybookDefinition.model_validate(
            _playbook(build_behavior={"feedback_target": "missing"})
        )


def test_feedback_targets_require_an_explicit_workflow_feedback_consumer():
    """UT-003: feedback routing fails closed when its target cannot receive it."""
    payload = _playbook(build_behavior={"feedback_target": "verify"})
    payload["steps"]["verify"].pop("input_artifacts")

    with pytest.raises(ValueError, match="feedback_target.*workflow_feedback"):
        PlaybookDefinition.model_validate(payload)


def test_human_feedback_delivery_requires_an_explicit_workflow_feedback_consumer():
    """UT-006: durable human feedback cannot route to an undeclared consumer."""
    payload = _playbook()
    payload["steps"]["build"]["human_tasks"] = [
        {
            "trigger": "confirm_output",
            "task_id": "review",
            "outcomes": {"request_changes": "verify"},
            "feedback_delivery": {
                "artifact": "workflow_feedback",
                "source_kind": "local_review",
            },
        }
    ]
    payload["steps"]["build"]["on"] = {"confirm_output": "build"}
    payload["steps"]["verify"].pop("input_artifacts")

    with pytest.raises(ValueError, match="feedback_delivery.*workflow_feedback"):
        PlaybookDefinition.model_validate(payload)


def test_publish_confirmation_requires_the_publish_capability():
    """UT-001: publishing contracts cannot be satisfied by an unrelated grant."""
    payload = _playbook(build_behavior={"publish_confirmation": True})
    payload["steps"]["build"]["capability_requests"] = ["cafe.github.read"]

    with pytest.raises(ValueError, match="cafe.pr.publish"):
        PlaybookDefinition.model_validate(payload)


def test_baton_completion_requires_workflow_complete_for_terminal_transition():
    """A baton-driven terminal route must be valid for the done owner."""
    payload = {
        "playbook": {"id": "terminal-baton"},
        "steps": {
            "publish": {
                "role": "operator",
                "skill": "phase",
                "behavior": {"completion": "baton"},
                "on": {"await_agent": "_done"},
            }
        },
    }

    with pytest.raises(ValueError, match="requires terminal transitions to use workflow_complete"):
        PlaybookDefinition.model_validate(payload)

    payload["steps"]["publish"]["on"] = {"workflow_complete": "_done"}
    model = PlaybookDefinition.model_validate(payload)

    assert model.steps["publish"].on == {"workflow_complete": "_done"}


def test_custom_named_publish_step_uses_declared_baton_and_receipt_contract(tmp_path):
    """UT-003/UT-004: completion and publish gates have no reserved step name."""
    playbook = _playbook(
        build_behavior={"completion": "baton", "publish_confirmation": True}
    )
    playbook["steps"]["build"]["capability_requests"] = ["cafe.pr.publish"]
    issue_dir = tmp_path / ".cafe" / "issues" / "custom"
    issue_dir.mkdir(parents=True)
    (issue_dir / "issue.yaml").write_text(
        "pr:\n  auto_create: true\n", encoding="utf-8"
    )

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=lambda *_args, **_kwargs: None,
    )

    assert runtime._is_baton_driven_step("build") is True
    assert runtime._required_capability_ids("build") == ["cafe.pr.publish"]
    assert runtime._is_baton_driven_step("verify") is False


def test_custom_named_publish_hook_accepts_declared_terminal_baton(tmp_path):
    """UT-004: native publish hooks consume the declaration, not ``pr``."""
    baton_file = tmp_path / "next_step.txt"
    baton_file.write_text(
        """{
  "version": 1,
  "from_step": "release",
  "to_owner": "done",
  "to_step": "done",
  "intent": "workflow_complete"
}""",
        encoding="utf-8",
    )

    assert _publish_requested(
        phase=object(),
        step_name="release",
        status_code="",
        context={
            "publish_confirmation": True,
            "next_step_path": str(baton_file),
        },
    )


_DEFAULT_WORKFLOW_STEPS = frozenset({"spec", "plan", "develop", "review", "pr"})
_STEP_IDENTITY_NAMES = frozenset({"step_name", "active_step", "current_step", "phase_name"})
_RUNTIME_LIFECYCLE_MODULES = (
    native_hooks,
    workflow_runtime,
    generic_phase,
    generic_workflow_step,
    agent_manager,
    cli_shared,
)


def _node_contains_step_identity(node):
    return any(
        (isinstance(child, ast.Name) and child.id in _STEP_IDENTITY_NAMES)
        or (isinstance(child, ast.Constant) and child.value == "step_name")
        for child in ast.walk(node)
    )


def _workflow_step_literals(node):
    return {
        child.value
        for child in ast.walk(node)
        if isinstance(child, ast.Constant) and child.value in _DEFAULT_WORKFLOW_STEPS
    }


def _fixed_name_step_policy_violations(module):
    """Return runtime branches that infer behavior from legacy workflow names."""
    tree = ast.parse(inspect.getsource(module))
    violations = []

    for node in ast.walk(tree):
        uses_step_identity = _node_contains_step_identity(node)
        fixed_names = _workflow_step_literals(node)
        if not uses_step_identity or not fixed_names:
            continue

        if isinstance(node, (ast.Compare, ast.IfExp)):
            violations.append((node.lineno, sorted(fixed_names)))
        elif isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            violations.append((node.lineno, sorted(fixed_names)))
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
        ):
            violations.append((node.lineno, sorted(fixed_names)))

    return violations


def test_runtime_and_lifecycle_modules_do_not_infer_behavior_from_default_step_names():
    """UT-010: bounded runtime/lifecycle policy forbids fixed-name behavior branches."""
    violations = {
        module.__name__: _fixed_name_step_policy_violations(module)
        for module in _RUNTIME_LIFECYCLE_MODULES
    }

    assert violations == {
        module.__name__: [] for module in _RUNTIME_LIFECYCLE_MODULES
    }
