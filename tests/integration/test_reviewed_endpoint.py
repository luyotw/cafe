"""Explicit review-only preparation ends at acceptance of the independent review."""

import hashlib
import json
from pathlib import Path
import shutil

import cafe

import pytest
from typer.testing import CliRunner
import yaml

from cafe.core.blackboard import ArtifactEntry, ArtifactKind
from cafe.core.human_task_records import HumanTaskRecordStore
from cafe.core.workflow_models import StepExecutionResult
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
from cafe.core.workspace_artifact import build_workspace_artifact
from cafe.playbooks.loader import PlaybookLoader, apply_issue_playbook_overrides
from cafe.ui.cli import app
from cafe.ui.human_tasks import apply_human_task_payload
from cafe.workflow_execution.automatic import automatic_executor_registry
from tests.integration.test_development_delivery_operations import local_action, git


def test_prepare_local_review_without_remote_or_integration_choices(local_action, monkeypatch):
    from tests.conftest import create_minimal_config

    root, _, _, _ = local_action
    create_minimal_config(root)
    monkeypatch.chdir(root)
    monkeypatch.setattr("cafe.ui.commands.lifecycle._offer_cli_update", lambda **kw: None)
    result = CliRunner().invoke(
        app,
        [
            "prepare",
            "prepared",
            "--playbook",
            "bug",
            "--no-interactive",
            "--no-check",
            "--input-method",
            "manual",
            "--local-review",
        ],
    )
    assert result.exit_code == 0, result.output + repr(result.exception)
    path = root / ".cafe/issues/prepared/issue.yaml"
    playbook = PlaybookLoader(project_root=root).load("bug")
    graph = apply_issue_playbook_overrides(playbook, path)
    target = graph["steps"]["review"]["on"]["await_agent"]
    assert graph["steps"][target]["assignee_type"] == "auto"
    assert "strategy" not in yaml.safe_load(path.read_text()).get("deliver", {})


@pytest.mark.parametrize(
    "decision",
    [
        "confirm",
        "single_step",
        "fix_now",
        "changed_evidence",
        "changed_head",
        "changed_choice",
        "changed_workspace",
        "unpassed",
        "oversize",
        "owner_policy",
        "changed_while_answering",
        "catalog_publication",
        "changed_presentation",
        "zh-TW",
    ],
)
def test_user_accepts_only_the_reviewed_custom_revision(local_action, monkeypatch, decision):
    root, _, _, _ = local_action
    issue = root / ".cafe/issues/local-endpoint"
    monkeypatch.chdir(root)
    issue.mkdir(parents=True, exist_ok=True)
    graph = PlaybookLoader(project_root=root).load("bug")
    names = {
        "develop": "repair_custom",
        "review": "verify_custom",
        "pr": "package_custom",
        "workspace": "checkout_material",
        "code": "test_record",
        "review_feedback": "verdict",
        "pr_result": "publication_text",
    }

    def rename(value):
        if isinstance(value, list):
            return [rename(v) for v in value]
        if isinstance(value, dict):
            return {names.get(k, k): rename(v) for k, v in value.items()}
        return names.get(value, value) if isinstance(value, str) else value

    # Install matching prompt-input declarations alongside the custom playbook so the
    # task command can resolve the same graph from disk at the decision boundary.
    skill_root = Path(cafe.__file__).parent / "data/skills"
    for skill in {step["skill"] for step in graph["steps"].values() if "skill" in step}:
        destination = root / ".cafe/skills" / skill
        shutil.copytree(skill_root / skill, destination, dirs_exist_ok=True)
        skill_file = destination / "SKILL.md"
        header, body = skill_file.read_text().split("---", 2)[1:]
        metadata = yaml.safe_load(header)
        if skill == "cafe-pr" and decision == "owner_policy":
            # This custom endpoint already binds its local presentation by task ID.
            tasks = metadata["workflow"]["human_tasks"]
            tasks[:] = [policy for policy in tasks if policy["id"] != "pr-review"]
            next(policy for policy in tasks if policy["id"] == "reviewed-revision")["id"] = "pr-review"
        for item in metadata.get("workflow", {}).get("prompt_inputs", []):
            if "artifacts" in item:
                item["artifacts"] = rename(item["artifacts"])
        skill_file.write_text("---\n" + yaml.safe_dump(metadata, sort_keys=False) + "---" + body)
    raw_path = PlaybookLoader(project_root=root)._resolve_path("bug")[1]
    local_playbook = root / ".cafe/playbooks/bug.yaml"
    local_playbook.parent.mkdir(parents=True, exist_ok=True)
    raw = yaml.safe_load(raw_path.read_text())
    if decision == "owner_policy":
        raw["steps"]["pr"]["local_review"].pop("presentation_task", None)
        graph["steps"]["pr"]["local_review"].pop("presentation_task", None)
    local_playbook.write_text(yaml.safe_dump(rename(raw)))
    graph = rename(graph)
    config = issue / "issue.yaml"
    config.write_text(yaml.safe_dump({"package_custom": {"endpoint": "local-review"}}))
    graph = apply_issue_playbook_overrides(graph, config)
    build = issue / "repair_custom/iteration_001"
    review = issue / "verify_custom/iteration_001"
    build.mkdir(parents=True, exist_ok=True)
    review.mkdir(parents=True, exist_ok=True)
    evidence = build / "output.md"
    evidence.write_text(
        "Targeted checks passed on macOS. Eleven failures also occurred at the parent."
    )
    verdict = review / "output.md"
    verdict.write_text(
        "Independent review passed. Linux assertions inspected. Native Linux unverified."
    )
    if decision == "oversize":
        evidence.write_text("a" * 570_000)
        verdict.write_text("b" * 480_000)
    calls = []

    def provider(step, definition, state, **kwargs):
        (review / "checklist.md").write_text("[x] Verify the exact revision.\n")
        calls.append(step)
        assert step == "verify_custom"
        (issue / "next_step.txt").write_text(json.dumps({"version": 1, "intent": "await_agent"}))
        return StepExecutionResult(
            response="await_agent",
            status_code="await_agent",
            artifacts={"verdict": str(verdict)},
            agent_invoked=True,
        )

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue,
        playbook=graph,
        executor=provider,
        automatic_registry=automatic_executor_registry(),
    )
    if decision == "zh-TW":
        runtime.blackboard.conversation_locale = "zh-TW"
    head = git(root, "rev-parse", "HEAD")
    workspace = build_workspace_artifact(
        repo=root,
        name="checkout_material",
        base_sha=git(root, "rev-parse", "HEAD^"),
        head_sha=head,
        version=1,
        producer_step="repair_custom",
    )
    ws = build / "workspace.json"
    ws.write_text(json.dumps(workspace.to_dict()))
    for name, kind, path in [
        ("test_record", ArtifactKind.DOCUMENT, evidence),
        ("checkout_material", ArtifactKind.WORKSPACE, ws),
    ]:
        runtime.blackboard.artifacts[name] = ArtifactEntry(
            name=name,
            kind=kind,
            path=str(path),
            version=1,
            updated_by="repair_custom",
            updated_at="2026-01-01T00:00:00Z",
            content_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            head_sha=head,
            base_sha=workspace.base_sha,
        )
    runtime.blackboard_store.save(runtime.blackboard)
    result = runtime.run(
        start_step="package_custom" if decision == "unpassed" else "verify_custom",
        single_step=decision == "single_step",
    )
    if decision == "single_step":
        result = runtime.run()
    records = HumanTaskRecordStore(issue)
    if decision == "oversize":
        assert not records.tasks() and (result.final_status_code == "AUTOMATIC_EXECUTOR_REJECTED")
        return
    if decision == "unpassed":
        assert not any(t.trigger == "confirm_output" for t in records.tasks())
        assert not calls
        return
    assert calls == ["verify_custom"], (result, calls)
    (task,) = records.tasks()
    assert task.trigger == "confirm_output"
    if decision in {"confirm", "zh-TW", "owner_policy"}:
        labels = {item["id"]: item["label"] for item in task.expected_result["decisions"]}
        assert ("完成" if decision == "zh-TW" else "finish") in labels["confirm"].lower()
        assert ("修訂" if decision == "zh-TW" else "revision") in labels["fix_now"].lower()
        assert ("提交" if decision == "zh-TW" else "commit") in task.prompt.split("\n\n", 1)[0].lower()
    for source in (head, workspace.base_sha, evidence.read_text(), verdict.read_text()):
        assert source in task.prompt
    if decision == "changed_evidence":
        evidence.write_text("replaced")
    if decision == "changed_head":
        git(root, "commit", "--allow-empty", "-m", "new revision")
    if decision == "changed_workspace":
        ws.write_text(ws.read_text() + "\n")
    if decision == "changed_choice":
        graph["steps"][task.step].pop("automatic")
    if decision == "changed_while_answering":
        complete = HumanTaskRecordStore.complete
        def change_selection(store, **kwargs):
            config.write_text(yaml.safe_dump({"package_custom": {"endpoint": "manual"}}))
            return complete(store, **kwargs)
        monkeypatch.setattr(HumanTaskRecordStore, "complete", change_selection)
    if decision == "changed_presentation":
        updated = yaml.safe_load(local_playbook.read_bytes())
        updated["steps"][task.step]["local_review"].pop("presentation_task")
        local_playbook.write_text(yaml.safe_dump(updated))
    if decision == "catalog_publication":
        import fcntl
        import os
        from cafe.core import packet_io
        from cafe.utils.config import get_global_cafe_dir

        replace = packet_io.os.replace
        catalog_protected = []
        def check_replacement(source, destination):
            if Path(destination) == records.file_path:
                fd = os.open(get_global_cafe_dir().parent, os.O_RDONLY)
                try:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        catalog_protected.append(True)
                    else:
                        catalog_protected.append(False)
                finally:
                    os.close(fd)
            return replace(source, destination)
        monkeypatch.setattr(packet_io.os, "replace", check_replacement)
    applied = apply_human_task_payload(
        issue_dir=issue,
        playbook_data=graph,
        blackboard=runtime.blackboard,
        from_step=task.step,
        trigger=task.trigger,
        source="interactive",
        raw_payload={
            "human_task_id": task.id,
            "decision": "fix_now" if decision == "fix_now" else "confirm",
            **({"feedback": "revise the repair"} if decision == "fix_now" else {}),
        },
    )
    if decision == "catalog_publication":
        assert catalog_protected and catalog_protected[0]
    if decision.startswith("changed"):
        assert applied.rejection and records.get_task(task.id).status.value == "pending"
    else:
        assert applied.target == ("repair_custom" if decision == "fix_now" else "done")
        assert records.get_task(task.id).status.value == "completed"


@pytest.mark.parametrize("drift", [None, "source", "target", "evidence", "workspace", "dispatch", "recover"])
def test_review_to_prepared_local_approval(local_action, monkeypatch, drift):
    from cafe.delivery.contracts import ActionProposal
    from cafe.delivery.selection import proposal_path

    root, destination, _, snapshot = local_action
    monkeypatch.chdir(root)
    issue = root / ".cafe/issues/prepared-local"
    issue.mkdir(parents=True)
    policy = snapshot.proposal
    config = issue / "issue.yaml"
    config.write_text(yaml.safe_dump({
        "playbook_id": "standard",
        "pr": {"auto_create": False},
        "deliver": {
            "mode": "local", "destination": str(destination),
            "target_branch": policy.target_branch, "strategy": policy.strategy,
            "verification": policy.verification.model_dump(mode="json"),
        },
    }))
    graph = apply_issue_playbook_overrides(PlaybookLoader(project_root=root).load("standard"), config)
    calls = []

    def provider(step, definition, state, **kwargs):
        calls.append(step)
        assert step == "review", "local preparation started a PR provider"
        (review / "checklist.md").write_text("[x] Verify the exact revision.\n")
        (issue / "next_step.txt").write_text('{"version":1,"intent":"await_agent"}')
        return StepExecutionResult(
            response="await_agent", status_code="await_agent",
            artifacts={"review_feedback": str(verdict)}, agent_invoked=True,
        )

    runtime = BlackboardWorkflowRuntime(issue_dir=issue, playbook=graph, executor=provider,
                                       automatic_registry=automatic_executor_registry())
    build = issue / "develop/iteration_001"
    review = issue / "review/iteration_001"
    build.mkdir(parents=True)
    review.mkdir(parents=True)
    evidence = build / "output.md"
    evidence.write_text("Saved tests passed on the reviewed revision.\n")
    verdict = review / "output.md"
    verdict.write_text("Independent review passed.\n")
    workspace = build_workspace_artifact(
        repo=root, name="workspace", base_sha=policy.target_oid,
        head_sha=policy.source_oid, version=1, producer_step="develop",
    )
    ws = build / "workspace.json"
    ws.write_text(json.dumps(workspace.to_dict()))
    for name, kind, path in [("code", ArtifactKind.DOCUMENT, evidence),
                              ("workspace", ArtifactKind.WORKSPACE, ws)]:
        runtime.blackboard.artifacts[name] = ArtifactEntry(
            name=name, kind=kind, path=str(path), version=1, updated_by="develop",
            content_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            head_sha=workspace.head_sha, base_sha=workspace.base_sha,
        )
    runtime.blackboard_store.save(runtime.blackboard)

    def answer(task, decision):
        return apply_human_task_payload(
            issue_dir=issue, playbook_data=graph,
            blackboard=runtime.blackboard_store.load_read_only(),
            from_step=task.step, trigger=task.trigger, source="interactive",
            raw_payload={"decision": decision, "human_task_id": task.id},
        )

    result = runtime.run(start_step="review")
    records = HumanTaskRecordStore(issue)
    summaries = [task for task in records.tasks() if task.step == "pr"]
    assert len(summaries) == 1, result
    summary = summaries[0]
    assert evidence.read_text().strip() in summary.prompt
    assert verdict.read_text().strip() in summary.prompt
    if drift == "recover":
        entry = runtime.blackboard.artifacts["local_preparation"]
        path = root / entry.path
        prepared = path.read_bytes()
        # Simulate a stop before artifact publication in the same automatic visit.
        path.unlink()
        runtime.blackboard.artifacts.pop(entry.name)
        runtime.blackboard_store.save(runtime.blackboard)
        result = runtime.run(start_step="pr")
        assert [task.id for task in records.tasks() if task.step == "pr"] == [summary.id], result
        assert path.read_bytes() == prepared
    assert answer(summary, "confirm").rejection is None
    result = runtime.run()
    approvals = [task for task in records.tasks() if task.policy_id == "delivery-review"]
    assert len(approvals) == 1, result
    approval = approvals[0]
    proposal = ActionProposal.model_validate_json(proposal_path(issue, approval.id).read_bytes())
    assert calls == ["review"]
    assert (proposal.source_oid, proposal.target_oid) == (policy.source_oid, policy.target_oid)
    assert str(destination) in approval.prompt
    assert evidence.read_text().strip() in approval.prompt
    assert proposal.digest in approval.prompt
    if drift in {"source", "target"}:
        git(root if drift == "source" else destination, "commit", "--allow-empty", "-m", "Drift")
    if drift in {"evidence", "workspace"}:
        changed = evidence if drift == "evidence" else ws
        changed.write_text(changed.read_text() + "\n")
    target_before = git(destination, "rev-parse", "HEAD")
    assert (answer(approval, "integrate_only").rejection is None) == (drift in {None, "dispatch", "recover"})
    assert git(destination, "rev-parse", "HEAD") == target_before
    if drift == "dispatch":
        git(destination, "merge", "--ff-only", policy.source_oid)
        git(destination, "commit", "--allow-empty", "-m", "Unapproved destination change")
        changed_target = git(destination, "rev-parse", "HEAD")
        result = runtime.run()
        assert not result.completed
        assert not any(task.policy_id == "delivery-outcome" for task in records.tasks())
        assert git(destination, "rev-parse", "HEAD") == changed_target
