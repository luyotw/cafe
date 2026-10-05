"""I1-I5/I8: durable compact authority, resume and declaration-based gates."""

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import json
import subprocess
import sys

import pytest

from tests.unit.test_compact_contract import compact_request, compact_proposal, activate
from tests.unit._kickoff_test_support import SCRIPT_ROOT
from cafe.manager.api import ReplaceConfirmedContract, replace_confirmed_contract


def scope(issue, root, boundary="resume", round_id="round"):
    return subprocess.run([sys.executable, str(SCRIPT_ROOT / "check_execution_scope.py"),
        "--issue-dir", str(issue), "--root", str(root), "--boundary", boundary,
        "--round-id", round_id, "--parent-id", "parent", "--output", str(issue / "checkpoint.json")],
        capture_output=True, text=True, timeout=30)


def test_scope_expansion_requires_current_user_revision_and_preserves_original_baseline(compact_request, compact_proposal):
    root = Path(compact_request["project_root"])
    issue = root / ".cafe/issues/sample"
    assert scope(issue, root).returncode != 0
    first = activate(issue, compact_proposal)
    (root / "app.py").write_text("approved subset")
    passed = scope(issue, root)
    assert passed.returncode == 0 and json.loads(passed.stdout)["passed"]
    (root / "new.py").write_text("requires expansion")
    blocked = scope(issue, root, "before_review")
    assert blocked.returncode != 0
    assert any(f["path"] == "new.py" for f in json.loads(blocked.stdout)["findings"])
    expanded = deepcopy(compact_proposal)
    expanded["file_scope"]["paths"].append("new.py")
    with pytest.raises(ValueError):
        replace_confirmed_contract(ReplaceConfirmedContract(issue, "sample", "workflow", "manager",
            datetime.now(timezone.utc), expanded, first.contract_sha256, "user_reconfirmation"))
    replacement = replace_confirmed_contract(ReplaceConfirmedContract(issue, "sample", "workflow", "user",
        datetime.now(timezone.utc), expanded, first.contract_sha256, "user_reconfirmation"))
    current = scope(issue, root, "before_review", "new-round")
    assert current.returncode == 0
    receipt = json.loads(current.stdout)
    assert receipt["revision"] == 2 and receipt["authority_digest"] == replacement.contract_sha256
    from cafe.manager.api import confirmed_contract_snapshot
    assert confirmed_contract_snapshot(issue)["file_scope"]["baseline_commit"] == compact_proposal["file_scope"]["baseline_commit"]


def test_custom_compact_graph_does_not_acquire_single_native_review(compact_request, compact_proposal):
    from cafe.manager.file_scope import execution_scope_projection
    root = Path(compact_request["project_root"])
    issue = root / ".cafe/issues/sample"
    activate(issue, compact_proposal)
    context = execution_scope_projection(issue, root)
    assert context["review_policy"] is None


def test_runtime_requires_native_terminal_proof_even_with_status_completion(compact_request, compact_proposal):
    from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
    from cafe.core.workflow_models import StepExecutionResult
    from cafe.manager.file_scope import execution_scope_projection
    root = Path(compact_request["project_root"])
    issue = root / ".cafe/issues/sample"
    activate(issue, compact_proposal)
    context = execution_scope_projection(issue, root)
    playbook = {"playbook": {"id": "custom"}, "roles": {"operator": {}}, "steps": {
        "inspect_custom": {"role": "operator", "skill": "plain", "execution": {
            "checkpoints": ["before_review"], "review_policy": "single_native", "review_evidence_artifact": "custom-review.json"},
            "on": {"await_agent": "_done"}, "human_tasks": [
                {"trigger": "need_clarification", "pattern": "revision_feedback", "resume": "inspect_custom"}]}}}
    def executor(step, definition, board, **kwargs):
        (issue / "next_step.txt").write_text(json.dumps({"version": 1, "intent": "await_agent"}))
        return StepExecutionResult(response="Ready for review", artifacts={})
    runtime = BlackboardWorkflowRuntime(issue_dir=issue, playbook=playbook, executor=executor, execution_context=context)
    result = runtime.run()
    assert not result.completed and result.final_status_code == "NATIVE_REVIEW_BLOCKED"


def native_context(compact_request, tmp_path, monkeypatch):
    import os
    import yaml
    from tests.unit._kickoff_test_support import load_kickoff_module
    from cafe.manager.file_scope import execution_scope_projection
    root = Path(compact_request["project_root"])
    binary = tmp_path / "provider-bin"
    binary.mkdir()
    cli = binary / "claude"
    cli.write_text("#!/bin/sh\nif [ \"$1\" = --version ]; then echo fixture-provider; exit 0; fi\nexit 2\n")
    cli.chmod(0o755)
    monkeypatch.setenv("PATH", str(binary) + os.pathsep + os.environ["PATH"])
    compact_request["compact_inputs"]["phases"][0]["chain"] = [{"cli": "claude", "model": "test"}]
    compact_request["compact_inputs"]["review_configuration"].update(cli="claude", provider_version="fixture-provider")
    compact_request["model_assessments"][0]["provider"] = "claude"
    playbook_file = root / ".cafe/playbooks/selected.yaml"
    playbook = yaml.safe_load(playbook_file.read_text())
    step = playbook["steps"]["build"]
    step.update(allowed_tools=["Read", "Glob", "Grep", "Bash", "Write", "Agent"],
        behavior={"completion": "baton"}, max_attempts_per_cycle=2,
        execution={"checkpoints": ["resume", "before_review"], "review_policy": "single_native",
                   "review_evidence_artifact": "native-review.json"})
    step["on"].update(await_agent="build", manual_handoff="build", need_clarification="build", workflow_complete="_done")
    playbook["skills"]["workflow"]["steps"] = {"build": {"mode": "extend", "skills": ["cafe-develop_single_review"]}}
    step["human_tasks"] = [{"trigger": "manual_handoff", "task_id": "iteration-limit", "outcomes": {"resume": "build"}}]
    playbook_file.write_text(yaml.safe_dump(playbook))
    owner = load_kickoff_module("kickoff_inputs")
    discovery = owner.discover_kickoff(compact_request, config_dir=tmp_path / "prefs", cache_dir=tmp_path / "cache")
    assert discovery.get("contract_mode") == "compact", discovery.get("catalog", {}).get("diagnostics")
    proposal = owner.assemble_kickoff(compact_request, discovery=discovery)
    assert proposal["status"] == "ready", proposal
    issue = root / ".cafe/issues/sample"
    activate(issue, proposal["proposal"])
    return root, issue, playbook, execution_scope_projection(issue, root)


def test_corrected_versions_each_receive_one_checkpointed_independent_review(compact_request, tmp_path, monkeypatch):
    from cafe.agents.cli.claude import ClaudeCLI
    from cafe.core.types import AgentConfig, AgentCLI
    from cafe.core.workflow_models import StepExecutionResult
    from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
    from cafe.core.execution_checkpoints import require_verified_review
    root, issue, playbook, context = native_context(compact_request, tmp_path, monkeypatch)
    calls, receipts = [], []
    adapter = ClaudeCLI(AgentConfig(name="parent", cli=AgentCLI.CLAUDE, model="test",
                                   native_review_configuration=context["review_configuration"]))
    def provider(step, definition, board, **kwargs):
        round_number = len(calls) + 1
        calls.append(round_number)
        (root / "app.py").write_text(f"value = {round_number}\n")
        checked = scope(issue, root, "before_review", f"round-{round_number}")
        assert checked.returncode == 0
        receipt = json.loads(checked.stdout)
        receipts.append(receipt)
        assert receipt["passed"]
        # External provider transport fixture; the production adapter parses it.
        findings = [{"severity": "blocking", "detail": "Correct value"}] if round_number == 1 else []
        conclusion = {"findings": findings, "targeted_tests": ["approved value check passed"]}
        actual_check = subprocess.run([sys.executable, "-c", "import runpy; assert runpy.run_path('app.py')['value'] > 0"], cwd=root)
        assert actual_check.returncode == 0
        invocation_id = f"native-{round_number}"
        stream = [json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use",
            "name": "Agent", "id": invocation_id, "input": {"subagent_type": "cafe_reviewer",
                "prompt": "CAFE_REVIEW_CHECKPOINT:" + receipt["receipt_id"]}}]}}),
            json.dumps({"type": "user", "message": {"content": [{"type": "tool_result",
                "tool_use_id": invocation_id, "content": json.dumps(conclusion)}]}})]
        observed = adapter.native_review_observations(stream)
        iteration = issue / step / f"iteration_{round_number:03}"
        iteration.mkdir(parents=True)
        evidence = {"version": 1, "round_id": receipt["round_id"], "checkpoint": receipt,
            "invocations": [{"reviewer_id": invocation_id, "parent_id": "parent",
                "configuration": context["review_configuration"], "terminal": "result", "exit_status": 0,
                "result_reference": invocation_id, **conclusion}]}
        (iteration / "native-review.json").write_text(json.dumps(evidence))
        (iteration / "native_invocations.json").write_text(json.dumps({"version": 1, "parent_id": "parent", "observations": observed}))
        (iteration / "output.md").write_text("Bounded review round\n")
        baton = ({"version": 1, "to_owner": "agent", "to_step": step, "intent": "manual_handoff"} if round_number == 1 else
                 {"version": 1, "to_owner": "done", "to_step": "done", "intent": "workflow_complete"})
        (issue / "next_step.txt").write_text(json.dumps(baton))
        return StepExecutionResult(response="", artifacts={})
    runtime = BlackboardWorkflowRuntime(issue_dir=issue, playbook=playbook, executor=provider, execution_context=context)
    result = runtime.run()
    assert result.completed and calls == [1, 2]
    assert receipts[0]["receipt_id"] != receipts[1]["receipt_id"]
    assert receipts[0]["snapshot"] != receipts[1]["snapshot"]
    current = json.loads((issue / "execution_review.json").read_text())
    require_verified_review(context, current)
    assert json.loads((issue / "build/iteration_001/native-review.json").read_text())["invocations"][0]["findings"]
    (root / "app.py").write_text("value = 3\n")
    with pytest.raises(ValueError):
        require_verified_review(context, current)


def test_native_review_corrections_stop_at_declared_budget(compact_request, tmp_path, monkeypatch):
    from cafe.core.workflow_models import StepExecutionResult
    from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
    root, issue, playbook, context = native_context(compact_request, tmp_path, monkeypatch)
    calls = []
    def provider(step, definition, board, **kwargs):
        calls.append(step)
        (issue / "next_step.txt").write_text(json.dumps({"version": 1, "to_owner": "agent", "to_step": step, "intent": "manual_handoff"}))
        return StepExecutionResult(response="Reviewer unavailable; correction remains blocked", artifacts={})
    runtime = BlackboardWorkflowRuntime(issue_dir=issue, playbook=playbook, executor=provider, execution_context=context)
    result = runtime.run()
    assert not result.completed and result.final_status_code == "ITERATION_LIMIT_REACHED"
    assert calls == ["build", "build"]
    assert not (issue / "execution_review.json").exists()


@pytest.mark.parametrize("boundary", ["before_review", "resume", "before_delivery"])
def test_restored_committed_violation_blocks_all_semantic_boundaries(compact_request, compact_proposal, boundary):
    from tests.unit.test_compact_delivery import git
    root = Path(compact_request["project_root"])
    issue = root / ".cafe/issues/sample"
    activate(issue, compact_proposal)
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.org")
    outside = root / "outside.py"
    outside.write_text("unapproved")
    git(root, "add", "outside.py")
    git(root, "commit", "-qm", "unapproved change")
    git(root, "rm", "outside.py")
    git(root, "commit", "-qm", "restore original tree")
    assert not git(root, "status", "--porcelain")
    blocked = scope(issue, root, boundary)
    assert blocked.returncode != 0
    assert any(f["path"] == "outside.py" for f in json.loads(blocked.stdout)["findings"])


def test_unconfirmed_public_launcher_never_invokes_work(compact_request):
    root = Path(compact_request["project_root"])
    issue = root / ".cafe/issues/sample"
    from cafe.core.blackboard import BlackboardStore
    BlackboardStore(issue).load_or_create("build", playbook_id="selected")
    launched = subprocess.run([sys.executable, str(SCRIPT_ROOT / "run_workflow.py"),
        "--issue", "sample", "--playbook", "selected", "--manager-mode", "unattended",
        "--fresh-facts", "{}"], cwd=root, capture_output=True, text=True, timeout=30)
    assert launched.returncode != 0
    assert not (root / "app.py").exists()
    assert not (issue / "execution_context.json").exists()


def test_public_step_factory_keeps_confirmed_chain_when_defaults_are_broken(compact_request, tmp_path, monkeypatch):
    import yaml
    from cafe.ui.cli_shared import _build_workflow_step_executor
    from cafe.phases.generic_phase import GenericPhase
    from cafe.skills.loader import SkillLoader
    from cafe.utils.config import ConfigManager
    from cafe.agents.executor import AgentExecutor
    from cafe.core.types import AgentResponse, TokenUsage
    from cafe.manager.file_scope import execution_scope_projection
    from tests.unit._kickoff_test_support import load_kickoff_module
    root = Path(compact_request["project_root"])
    file = root / ".cafe/playbooks/selected.yaml"
    playbook = yaml.safe_load(file.read_text())
    playbook["roles"]["operator"] = {"default_agent": "operator"}
    file.write_text(yaml.safe_dump(playbook))
    owner = load_kickoff_module("kickoff_inputs")
    discovery = owner.discover_kickoff(compact_request, config_dir=tmp_path / "prefs", cache_dir=tmp_path / "cache")
    proposal = owner.assemble_kickoff(compact_request, discovery=discovery)["proposal"]
    issue = root / ".cafe/issues/sample"
    activate(issue, proposal)
    context = execution_scope_projection(issue, root)
    (root / ".cafe/phases.yaml").write_text("invalid defaults")
    monkeypatch.chdir(root)
    config = ConfigManager(root / ".cafe")
    config._config = config.get_default_config()
    dispatched = []
    def provider(executor, *args, **kwargs):
        dispatched.append((executor.config.cli.value, executor.config.model))
        return AgentResponse(response="complete", token_usage=TokenUsage(), cli=executor.config.cli)
    monkeypatch.setattr(AgentExecutor, "execute", provider)
    engine = _build_workflow_step_executor(config_manager=config, issue_dir=issue,
        issue_name="sample", playbook_data=playbook, generic_phase=GenericPhase(SkillLoader(project_root=root)),
        phase_name="build", execution_chain=context["phase_chains"]["build"], stream_agent_output=False)
    engine.agent_manager.execute("operator", "bounded provider fixture", phase_name="build")
    assert dispatched == [("codex", "test")]


def test_native_preparation_checks_explicit_backup_before_confirmation(compact_request, tmp_path, monkeypatch):
    from tests.unit._kickoff_test_support import load_kickoff_module
    native_context(compact_request, tmp_path, monkeypatch)
    compact_request['issue_name'] = 'unconfirmed-backup'
    compact_request['compact_inputs']['phases'][0]['chain'].append({'cli': 'codex', 'model': 'other'})
    other = deepcopy(compact_request['model_assessments'][0])
    other['model'] = 'other'
    other['provider'] = 'codex'
    compact_request['model_assessments'].append(other)
    owner = load_kickoff_module('kickoff_inputs')
    discovered = owner.discover_kickoff(compact_request, config_dir=tmp_path / 'prefs', cache_dir=tmp_path / 'cache')
    assembled = owner.assemble_kickoff(compact_request, discovery=discovered)
    assert assembled['status'] == 'incomplete'
    assert any('native_review_configuration' in gap['requirement'] for gap in assembled['missing_decisions'])
    assert assembled['proposal'] is None


@pytest.mark.parametrize("merged_bytes", [256 * 1024, 256 * 1024 + 1])
def test_native_result_merge_respects_delivery_reader_budget(compact_request, tmp_path, monkeypatch, merged_bytes):
    from cafe.agents.cli.claude import ClaudeCLI
    from cafe.core.types import AgentConfig, AgentCLI
    from cafe.core.workflow_models import StepExecutionResult
    from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
    from cafe.core.execution_checkpoints import load_review_evidence, require_verified_review
    from cafe.core.packet_io import canonical_json
    root, issue, playbook, context = native_context(compact_request, tmp_path, monkeypatch)
    adapter = ClaudeCLI(AgentConfig(name="parent", cli=AgentCLI.CLAUDE, model="test",
                                   native_review_configuration=context["review_configuration"]))
    def provider(step, definition, board, **kwargs):
        (root / "app.py").write_text("value = 1\n")
        checked = scope(issue, root, "before_review")
        assert checked.returncode == 0
        receipt = json.loads(checked.stdout)
        def records(detail):
            conclusion = {"findings": [{"severity": "nonblocking", "detail": detail}],
                          "targeted_tests": ["approved value check passed"]}
            stream = [json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use",
                "name": "Agent", "id": "child", "input": {"subagent_type": "cafe_reviewer",
                "prompt": "CAFE_REVIEW_CHECKPOINT:" + receipt["receipt_id"]}}]}}),
                json.dumps({"type": "user", "message": {"content": [{"type": "tool_result",
                "tool_use_id": "child", "content": json.dumps(conclusion)}]}})]
            observations = {"version": 1, "parent_id": "parent", "observations": adapter.native_review_observations(stream)}
            evidence = {"version": 1, "round_id": receipt["round_id"], "checkpoint": receipt,
                "invocations": [{"reviewer_id": "child", "parent_id": "parent",
                    "configuration": context["review_configuration"], "terminal": "result", "exit_status": 0,
                    "result_reference": "child", **conclusion}], "producer_note": ""}
            return evidence, observations
        evidence, observations = records("x")
        overhead = len(canonical_json({**evidence, "native_observations": observations}))
        padding, remainder = divmod(merged_bytes - overhead, 2)
        evidence, observations = records("x" * (padding + 1))
        evidence["producer_note"] = "x" * remainder
        assert len(canonical_json({**evidence, "native_observations": observations})) == merged_bytes
        iteration = issue / step / "iteration_001"
        iteration.mkdir(parents=True)
        (iteration / "native-review.json").write_bytes(canonical_json(evidence))
        (iteration / "native_invocations.json").write_bytes(canonical_json(observations))
        (iteration / "output.md").write_text("Bounded native result\n")
        (issue / "next_step.txt").write_text(json.dumps({"version": 1, "to_owner": "done", "to_step": "done", "intent": "workflow_complete"}))
        return StepExecutionResult(response="", artifacts={})
    result = BlackboardWorkflowRuntime(issue_dir=issue, playbook=playbook, executor=provider, execution_context=context).run()
    if merged_bytes > 256 * 1024:
        assert not result.completed and result.final_status_code == "NATIVE_REVIEW_BLOCKED"
        assert not (issue / "execution_review.json").exists()
    else:
        assert result.completed
        current = load_review_evidence(issue / "execution_review.json")
        require_verified_review(context, current)
