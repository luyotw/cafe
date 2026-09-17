"""E2E tests for default-playbook PR step via workflow runtime (no PRPhase)."""

from __future__ import annotations

import json
import re
import subprocess
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from cafe.core.blackboard import BlackboardStore, HandoffIntent, HandoffOwner
from cafe.core.human_task_records import HumanTaskRecordStore
from cafe.core.types import AgentCLI, TokenUsage
from cafe.core.workflow_models import StepExecutionResult
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
from cafe.phases.generic_phase import GenericPhase
from cafe.phases.generic_workflow_step import GenericWorkflowStepExecutor
from cafe.playbooks.loader import PlaybookLoader
from cafe.skills.loader import SkillLoader
from cafe.skills.native_bridge import NativeSkillBridge
from cafe.utils.phase_config import PhaseStepModelResolution
from cafe.verification import run_verification

pytestmark = pytest.mark.usefixtures("cached_builtin_playbook_models")


def test_pr_publish_generic_journey_preserves_outputs_and_correlates_receipt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import cafe.core.capabilities as cap_mod

    output_file = tmp_path / ".cafe/issues/demo/pr/iteration_001/output.md"
    output_file.parent.mkdir(parents=True)
    output_file.write_text("# PR\n", encoding="utf-8")

    class Result:
        returncode = 0
        stderr = ""
        stdout = (
            '{"action":"created","pr_number":"42",'
            '"pr_url":"https://github.com/acme/widgets/pull/42"}\n'
        )

    monkeypatch.setattr(cap_mod.subprocess, "run", lambda *_args, **_kwargs: Result())
    registry = cap_mod.load_capability_registry([cap_mod._package_capabilities_dir()])
    run = cap_mod.run_capability_request(
        repo_root=tmp_path,
        registry=registry,
        capability_request={
            "capability": "cafe.pr.publish",
            "args": {
                "output": ".cafe/issues/demo/pr/iteration_001/output.md",
                "base": "main",
            },
            "effects": {
                "browser_open": [],
                "network_destinations": ["github.com", "api.github.com"],
                "writes": [
                    ".cafe/issues/demo/pr/iteration_001/output.md",
                    ".git",
                    ".cafe/issues/demo",
                ],
            },
            "credentials": ["gh"],
            "permissions": {
                "network": ["github.com", "api.github.com"],
                "writes": [
                    ".cafe/issues/demo/pr/iteration_001/output.md",
                    ".git",
                    ".cafe/issues/demo",
                ],
            },
        },
        output_file=output_file,
    )

    assert run.receipt["success"] is True
    assert run.receipt["outputs"] == {
        "pr_url": "https://github.com/acme/widgets/pull/42",
        "pr_number": "42",
        "action": "created",
    }
    assert run.receipt["request_fingerprint"]
    assert run.receipt["manifest"]["id"] == "cafe.pr.publish"
    assert run.receipt["decision"]["outcome"] == "allow"
    assert run.receipt["outcome"] == "success"


def _load_default_playbook() -> dict:
    return PlaybookLoader().load("standard")


def _write_baton(
    issue_dir: Path,
    *,
    from_step: str,
    to_owner: HandoffOwner,
    to_step: str,
    intent: HandoffIntent,
    status_code: str = "confirmed",
) -> None:
    store = BlackboardStore(issue_dir)
    state = store.load_or_create(from_step)
    store.update_handoff_contract(
        state,
        from_step=from_step,
        to_owner=to_owner,
        to_step=to_step,
        intent=intent,
        status_code=status_code,
        source="test.executor",
    )


def _seed_pr_artifacts(issue_dir: Path, *, auto_create: bool = True) -> None:
    spec_file = issue_dir / "spec" / "iteration_001" / "output.md"
    plan_file = issue_dir / "plan" / "iteration_001" / "output.md"
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text("# Spec\n", encoding="utf-8")
    plan_file.write_text("# Plan\n", encoding="utf-8")
    store = BlackboardStore(issue_dir)
    state = store.load_or_create("pr")
    store.set_artifact(state, "spec", str(spec_file))
    store.set_artifact(state, "plan", str(plan_file))
    (issue_dir / "issue.yaml").write_text(
        "base_branch: main\n"
        "pr:\n"
        f"  auto_create: {str(auto_create).lower()}\n",
        encoding="utf-8",
    )


@pytest.mark.e2e
def test_pr_runtime_routes_generic_success_receipt_without_verified_url(
    tmp_path: Path,
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "issue-pr-e2e"
    playbook = _load_default_playbook()
    assert playbook["steps"]["pr"]["capability_requests"] == ["cafe.pr.publish"]
    _seed_pr_artifacts(issue_dir)

    def executor(step_name: str, step_def: dict, state: object) -> StepExecutionResult:
        if step_name != "pr":
            return StepExecutionResult(response="skip", artifacts={})
        _write_baton(
            issue_dir,
            from_step="pr",
            to_owner=HandoffOwner.USER,
            to_step="user",
            intent=HandoffIntent.CONFIRM_OUTPUT,
        )
        return StepExecutionResult(
            response="done",
            artifacts={"pr": str(issue_dir / "pr" / "iteration_001" / "output.md")},
            events=[
                {
                    "type": "capability_receipt",
                    "capability": "cafe.pr.publish",
                    "success": True,
                    "correlation_id": "test-pr-e2e",
                    "category": None,
                    "code": None,
                }
            ],
        )

    runtime = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=playbook,
        executor=executor,
    )
    result = runtime.run(start_step="pr", max_transitions=5)

    assert result.completed is False
    assert result.final_step == "pr"
    assert result.final_status_code == "confirmed"
    assert len(HumanTaskRecordStore(issue_dir).tasks()) == 1


@pytest.mark.e2e
@pytest.mark.parametrize("auto_create", [True, False])
@pytest.mark.parametrize("with_driver_contract", [False, True])
def test_pr_review_handoff_tracks_published_or_local_only_journey(
    tmp_path: Path,
    auto_create: bool,
    with_driver_contract: bool,
) -> None:
    """Integration 4: #467 publication has identical Driver-free outcomes."""
    issue_dir = tmp_path / ".cafe" / "issues" / f"review-{auto_create}-{with_driver_contract}"
    _seed_pr_artifacts(issue_dir, auto_create=auto_create)
    if with_driver_contract:
        driver_dir = issue_dir / "driver"
        driver_dir.mkdir()
        (driver_dir / "contract.json").write_text('{"not": "generic authority"}', encoding="utf-8")
    verified_url = "https://github.com/acme/widgets/pull/467"

    def executor(step_name: str, *_args: object, **_kwargs: object) -> StepExecutionResult:
        _write_baton(
            issue_dir,
            from_step=step_name,
            to_owner=HandoffOwner.USER,
            to_step="user",
            intent=HandoffIntent.CONFIRM_OUTPUT,
            status_code="BATON_CONFIRM_OUTPUT",
        )
        events = (
            [{"type": "pr_synced", "url": verified_url, "source": "capability"}]
            if auto_create
            else []
        )
        return StepExecutionResult(
            response="done",
            artifacts={"pr": str(issue_dir / "pr" / "iteration_001" / "output.md")},
            events=events,
        )

    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir,
        playbook=_load_default_playbook(),
        executor=executor,
    ).run(start_step="pr", max_transitions=5)

    assert result.final_status_code == "BATON_CONFIRM_OUTPUT"
    task = HumanTaskRecordStore(issue_dir).tasks()[0]
    if auto_create:
        assert f"Verified PR URL: {verified_url}" in task.prompt
    else:
        assert "Publication mode: local-only. No PR URL exists." in task.prompt


@pytest.mark.e2e
def test_declared_pr_feedback_source_records_and_delivers_each_comment_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """IT-001: current-invocation PR feedback is curated and consumed once."""
    from unittest.mock import patch

    from cafe.core.workflow_feedback import WorkflowFeedbackLedger
    from cafe.ui.cli_shared import _find_external_resume_step

    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "Test User"],
        check=True,
    )
    (tmp_path / ".gitignore").write_text(".cafe/\n.pytest_cache/\n__pycache__/\n", encoding="utf-8")
    source_file = tmp_path / "src" / "feedback.py"
    test_file = tmp_path / "tests" / "test_feedback.py"
    source_file.parent.mkdir()
    test_file.parent.mkdir()
    source_file.write_text("DELIVERED = True\n", encoding="utf-8")
    test_file.write_text(
        "from pathlib import Path\n\n"
        "def test_feedback_delivery_evidence() -> None:\n"
        "    assert Path('src/feedback.py').read_text().strip() == 'DELIVERED = True'\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-qm", "Add feedback fixture"],
        check=True,
    )
    monkeypatch.setattr(
        "cafe.phases.generic_workflow_step.load_phase_step_model",
        lambda **_kwargs: PhaseStepModelResolution(
            name="David",
            role="developer",
            clis=(("codex", "test-model"),),
            model="test-model",
            source="test",
            chain=("test",),
            name_source="test",
            role_source="test",
            clis_source="test",
        ),
    )
    issue_dir = tmp_path / ".cafe" / "issues" / "pr-feedback"
    issue_dir.mkdir(parents=True)
    (issue_dir / "issue.yaml").write_text(
        "{}\n",
        encoding="utf-8",
    )
    playbook = _load_default_playbook()
    playbook["steps"]["pr"]["capability_requests"] = []
    playbook["steps"]["pr"]["behavior"]["publish_confirmation"] = False
    playbook["steps"]["pr"].pop("workspace_input_artifact", None)
    ledger = WorkflowFeedbackLedger(issue_dir)

    class AgentManager:
        def __init__(self) -> None:
            self.prompts: list[str] = []
            self.agent = SimpleNamespace(
                config=SimpleNamespace(cli=AgentCLI.CODEX, session_id=None, model=None)
            )

        def get_agent(self, _name: str):
            return self.agent

        def execute(self, _name: str, prompt: str, **_kwargs):
            self.prompts.append(prompt)
            iteration_dir = Path(_kwargs["streaming_output_file"]).parent
            checklist = iteration_dir / "checklist.md"
            content = checklist.read_text(encoding="utf-8").replace("[ ]", "[x]")
            checklist.write_text(content, encoding="utf-8")
            projected = re.findall(
                r"^\[x\] `([^`]+)` .*source fingerprint: ([0-9a-f]{64}\)?)$",
                content,
                flags=re.MULTILINE,
            )
            entries = []
            head = subprocess.check_output(
                ["git", "-C", str(tmp_path), "rev-parse", "HEAD"], text=True
            ).strip()
            command = ["pytest", "-q", "tests/test_feedback.py"]
            for item_id, fingerprint in projected:
                entries.append(
                    f"### {item_id}\n\n- Status: completed\n"
                    f"- Source fingerprint: `{fingerprint.rstrip(')')}`\n"
                    "- Files: `src/feedback.py`, `tests/test_feedback.py`\n"
                    f"- Commit: `{head}`\n"
                    f"- Targeted evidence: command=`{' '.join(command)}`; exit=0; "
                    f"head=`{head}`\n- Remaining work: None.\n"
                    "- Next action: Review."
                )
            output = iteration_dir / "output.md"
            first_id = f"PRC-{sha256(b'github-pr:101:100').hexdigest()[:12].upper()}"
            second_id = f"PRC-{sha256(b'github-pr:101:101').hexdigest()[:12].upper()}"
            output.write_text(
                "## Todo List\n"
                f"- [ ] `{first_id}` — Source: `pr_comment` — Work: Curate the first boundary — "
                "Closure: addressed — Evidence: targeted pytest\n"
                f"- [ ] `{second_id}` — Source: `pr_comment` — Work: Curate the second boundary — "
                "Closure: addressed — Evidence: targeted pytest\n\n"
                "## Todo Progress\n\n" + "\n\n".join(entries) + "\n",
                encoding="utf-8",
            )
            assert (
                run_verification(
                    output_file=output,
                    command=command,
                    scope="targeted",
                    cwd=tmp_path,
                )[0]
                == 0
            )
            _write_baton(
                issue_dir,
                from_step="pr",
                to_owner=HandoffOwner.AGENT,
                to_step="develop",
                intent=HandoffIntent.MANUAL_HANDOFF,
            )
            return "await_agent", TokenUsage(), [], [], [], None

    class GitOperations:
        def get_current_branch(self) -> str:
            return "pr-feedback"

        def ensure_remote_base_ancestor(self, *_args, **_kwargs) -> None:
            return None

        def get_commits_between(self, *_args, **_kwargs) -> list[object]:
            return []

        def get_default_base_branch(self) -> str:
            return "main"

        def get_repo_root(self) -> Path:
            return tmp_path

    def build_phase() -> GenericPhase:
        data_root = Path(__file__).resolve().parents[2] / "src" / "cafe" / "data"
        skill_loader = SkillLoader(
            project_root=tmp_path,
            global_root=tmp_path / "global",
            builtin_root=data_root,
        )
        skill_loader.discover()
        return GenericPhase(
            skill_loader,
            skill_bridge=NativeSkillBridge(
                skill_loader,
                project_root=tmp_path,
                home_dir=tmp_path / "home",
            ),
        )

    delivery_manager = AgentManager()
    delivery_executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="pr-feedback",
        playbook=playbook,
        generic_phase=build_phase(),
        agent_manager=delivery_manager,
        git_ops=GitOperations(),
        role_agent_map={"developer": "David"},
    )
    with (
        patch("cafe.core.hooks.feedback.GitHubOps") as github_ops,
        patch(
            "cafe.core.hooks.feedback.get_all_pr_comments",
            return_value=[
                {"id": "100", "body": "Handle the first boundary.", "is_resolved": False},
                {"id": "101", "body": "Handle the second boundary.", "is_resolved": False},
            ],
        ),
    ):
        github_ops.return_value.get_pr_for_branch.return_value = {
            "number": 101,
            "url": "https://example.test/pr/101",
        }
        runtime_result = BlackboardWorkflowRuntime(
            issue_dir=issue_dir,
            playbook=playbook,
            executor=delivery_executor.execute_step,
        ).run(start_step="pr", single_step=True)

    assert len(delivery_manager.prompts) == 1, runtime_result
    assert "Handle the first boundary." in delivery_manager.prompts[0]
    assert "Handle the second boundary." in delivery_manager.prompts[0]
    assert ledger.pending(target_step="pr") == []
    state = BlackboardStore(issue_dir).load_or_create("pr")
    assert state.artifacts["pr_result"].updated_by == "pr"
    assert any(
        event.event_type == "transition"
        and event.data.get("from") == "pr"
        and event.data.get("to") == "develop"
        for event in state.events
    )
    assert (
        _find_external_resume_step(
            issue_dir=issue_dir,
            playbook_data=playbook,
            git_ops=GitOperations(),
        )
        is None
    )
