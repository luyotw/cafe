"""I2–I8: real regression evidence and unchanged bug runtime/human boundaries.

The deterministic agent is an external CLI substitute, not an evidence parser
or a claim about arbitrary model judgment. Local Git/test processes are real.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from cafe.agents.executor import AgentExecutionError
from cafe.core.blackboard import BlackboardStore
from cafe.core.capability_approvals import CapabilityApprovalService
from cafe.core.git import GitOperations
from cafe.core.hooks.native import GitHubPRCreator
from cafe.core.human_task_records import HumanTaskRecordStore, HumanTaskStatus
from cafe.core.types import AgentCLI, AgentConfig, TokenUsage
from cafe.core.workflow_feedback import WorkflowFeedbackLedger
from cafe.core.workflow_models import StepExecutionResult
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
from cafe.phases.generic_phase import GenericPhase
from cafe.phases.generic_workflow_step import GenericWorkflowStepExecutor
from cafe.playbooks.loader import PlaybookLoader
from cafe.skills.loader import SkillLoader
from cafe.skills.native_bridge import NativeSkillBridge
from cafe.ui.human_tasks import apply_capability_approval_payload, apply_human_task_payload

pytestmark = pytest.mark.usefixtures("cached_builtin_playbook_models")

REGRESSION = "from calc import twice\nassert twice(3) == 6\n"


def _process(repo, *command):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(command, cwd=repo, env=env, capture_output=True, text=True, timeout=30)


def _git(repo, *args):
    result = _process(repo, "git", *args)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


class DefectAgent:
    """Only the external agent execution is replaced; all delivery is production."""

    def __init__(self, repo, issue_dir):
        self.repo, self.issue_dir = repo, issue_dir
        self.agent = SimpleNamespace(
            config=AgentConfig(
                name="David",
                cli=AgentCLI.CODEX,
                session_id="bug-test",
                model=None,
            )
        )
        self.calls, self.inputs = [], []
        self.actions = {}
        self.interrupt_at = None
        self.inconclusive = None
        self.delivery_details_missing = False
        self.failed = False
        self.red = None
        self.green = None
        self.baseline = _git(repo, "rev-parse", "HEAD")
        self.identity = hashlib.sha256(REGRESSION.encode()).hexdigest()

    def get_agent(self, _name):
        return self.agent

    def get_last_cli(self):
        return AgentCLI.CODEX

    def get_last_session_id(self):
        return "bug-test"

    def get_failed_attempts(self):
        return (
            [{"cli": "codex", "session_id": "bug-test", "error_type": "rate_limit"}]
            if self.failed
            else []
        )

    def execute(self, name, prompt, *, streaming_output_file=None, **_kwargs):
        directory = Path(streaming_output_file).parent
        phase = directory.parent.name
        self.calls.append((phase, name))
        self.failed = False
        skill = {
            "diagnose": "cafe-bug-diagnose",
            "develop": "cafe-bug-repair",
            "review": "cafe-bug-review",
            "pr": "cafe-pr",
        }[phase]
        text = (self.repo / ".codex" / "skills" / skill / "SKILL.md").read_text()
        inputs = {}
        for line in text.splitlines():
            if line.startswith("- ") and ": " in line:
                key, raw = line[2:].split(": ", 1)
                if key.endswith("_file"):
                    path = Path(raw.strip())
                    if not path.is_absolute():
                        path = self.repo / path
                    if path.is_file():
                        inputs[key] = path.read_text()
        self.inputs.append((phase, inputs, prompt))
        checkpoint = directory / "output.md"
        if checkpoint.is_file():
            inputs["checkpoint_output_file"] = checkpoint.read_text()
        packet_path = directory / "delta_packet.json"
        if packet_path.exists():
            previous = json.loads(packet_path.read_text()).get("previous_output", {})
            if previous.get("path"):
                path = self.repo / previous["path"]
                if path.is_file():
                    inputs["previous_output_file"] = path.read_text()
        if phase in ("develop", "review"):
            assert self.identity in inputs["diagnosis_file"]
            assert json.loads(inputs["workspace_file"])["name"] == "workspace"
        action = self.actions.get((phase, sum(p == phase for p, _ in self.calls)))
        if phase == "diagnose":
            if self.interrupt_at == "before_red":
                (directory / "output.md").write_text(
                    "# Diagnosis checkpoint\nRED proof unfinished.\n"
                )
                self.interrupt_at = None
                self.failed = True
                raise AgentExecutionError("Interrupted before RED", error_type="rate_limit")
            if not self.red:
                isolated = self.repo.parent / "unfixed"
                isolated.mkdir(exist_ok=True)
                (isolated / "calc.py").write_text(
                    _git(self.repo, "show", f"{self.baseline}:calc.py")
                )
                (isolated / "regression.py").write_text(REGRESSION)
                self.red = _process(isolated, sys.executable, "regression.py")
                assert self.red.returncode == 1 and "AssertionError" in self.red.stderr
            report = (
                "# Verified diagnosis\n\n## Request boundary\n"
                "Fix twice(3): expected 6, observed 5.\n"
                f"Unfixed revision: {self.baseline}\nTest SHA256: {self.identity}\n"
                f"Command: {sys.executable} regression.py\nRED exit: {self.red.returncode}\n"
                "Cause: off-by-one return. Scope: calc.py and regression.py.\n"
                f"## Replayable regression\n```python\n{REGRESSION}```\n"
            )
            if self.inconclusive:
                failure, observed = self.inconclusive
                report = (
                    f"# Inconclusive diagnosis\nKnown trigger: twice(3).\n"
                    f"Failure class: {failure}; actual exit: {observed.returncode}\n"
                    "Expected behavior or defect reproduction remains unresolved.\n"
                    "No completed RED proof; repair remains pending.\n"
                )
        elif phase == "develop":
            if not self.green:
                (self.repo / "regression.py").write_text(REGRESSION)
                before = _process(self.repo, sys.executable, "regression.py")
                assert before.returncode == 1 and "AssertionError" in before.stderr
                (self.repo / "calc.py").write_text("def twice(value):\n    return value * 2\n")
                self.green = _process(self.repo, sys.executable, "regression.py")
                assert self.green.returncode == 0
                assert (
                    hashlib.sha256((self.repo / "regression.py").read_bytes()).hexdigest()
                    == self.identity
                )
                _git(self.repo, "add", "calc.py", "regression.py")
                _git(self.repo, "commit", "-m", "Fix demonstrated off-by-one defect")
            report = (
                f"# Repair summary\nDefect test: {self.identity}\nUnfixed: {self.baseline}\n"
                f"RED exit: {self.red.returncode}; GREEN exit: {self.green.returncode}\n"
                f"Commit: {_git(self.repo, 'rev-parse', 'HEAD')}\nScope: calc.py, regression.py\n"
            )
        elif phase == "review":
            assert self.identity in inputs["code_file"]
            assert self.green and self.green.returncode == 0
            report = (
                f"# Independent review\nAccepted regression {self.identity} and bounded repair.\n"
            )
        else:
            report = f"# PR draft\nVerified defect {self.identity}.\n"
        if action and action.get("target"):
            behavior = (
                PlaybookLoader(project_root=self.repo)
                .load("bug")["steps"][phase]
                .get("behavior", {})
            )
            prefix = behavior.get("feedback_todo_id_prefix", "BUGR")
            source = behavior.get("feedback_todo_source", "bug_review")
            item_id = f"{prefix}-001"
            if phase == "pr":
                rows = re.findall(r"use ID `([^`]+)` and Source `([^`]+)`", prompt)
                assert rows, "PR curator must receive canonical source identities"
            else:
                rows = [(item_id, source)]
            report += "\n## Todo List\n"
            for item_id, source in rows:
                report += (
                    f"- [ ] `{item_id}` — Source: `{source}` — Work: reassess proof — "
                    "Closure: valid defect evidence and independent review — "
                    "Evidence: same regression\n"
                )
        else:
            report += "\n## Todo List\nNo actionable work.\n"
        projected = re.findall(
            r"`([A-Z][A-Z0-9_-]+)`.*?source fingerprint: ([a-f0-9]{64})",
            (directory / "checklist.md").read_text(),
        )
        if projected:
            report += "\n## Todo Progress\n\n"
            for item, fingerprint in projected:
                report += (
                    f"### {item}\n- Status: completed\n- Source fingerprint: `{fingerprint}`\n"
                    "- Files: N/A (no repository changes)\n"
                    "- Commit: N/A (no repository changes): revalidated existing bounded repair\n"
                    "- Remaining work: None.\n- Next action: Independent assessment.\n\n"
                )
        if phase == "pr" and not self.delivery_details_missing:
            config = yaml.safe_load((self.issue_dir / "issue.yaml").read_text())
            github = config["pr"].get("auto_create") is True
            (directory / "delivery_request.json").write_text(
                json.dumps(
                    {
                        "mode": "github" if github else "local",
                        "strategy": "merge" if github else "ff-only",
                        "target_branch": "main",
                        "destination": "" if github else str(self.repo.parent / "destination"),
                        "issue_repository": "",
                        "verification": {"not_required_reason": "Offline integration; no post-merge checks in this fixture scope."},
                    }
                )
            )
        output = directory / "output.md"
        output.write_text(report)
        if self.interrupt_at == phase:
            self.interrupt_at = None
            self.failed = True
            raise AgentExecutionError("Interrupted evidence work", error_type="rate_limit")
        intent = (
            action.get("intent")
            if action
            else ("confirm_output" if phase == "pr" else "await_agent")
        )
        baton = {"version": 1, "intent": intent}
        if intent in ("need_clarification", "need_permission", "confirm_output"):
            baton.update(to_owner="user", to_step="user")
        elif action and action.get("target"):
            baton.update(to_owner="agent", to_step=action["target"])
        (self.issue_dir / "next_step.txt").write_text(json.dumps(baton))
        checklist = directory / "checklist.md"
        checklist.write_text(checklist.read_text().replace("[ ]", "[x]"))
        status = (
            "needs_changes"
            if intent == "manual_handoff"
            else (intent if intent in ("need_clarification", "need_permission") else "confirmed")
        )
        return status, TokenUsage(), [], [], [], None


@pytest.fixture
def journey(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    monkeypatch.setattr(
        "cafe.core.workflow_runtime.HumanTaskNotificationDispatcher.notify", lambda *_a, **_k: None
    )
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / ".gitignore").write_text(".cafe/\n.codex/\n__pycache__/\n")
    (repo / "calc.py").write_text("def twice(value):\n    return value * 2 - 1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "Initial defect")
    _git(repo, "checkout", "-b", "bug-repair")
    _git(repo, "worktree", "add", str(repo.parent / "destination"), "main")
    _git(repo, "remote", "add", "origin", "https://github.com/example/defect.git")
    issue_dir = repo / ".cafe" / "issues" / "defect"
    issue_dir.mkdir(parents=True)
    (issue_dir / "issue.yaml").write_text(
        yaml.safe_dump(
            {
                "playbook_id": "bug",
                "pr": {"auto_create": False},
                "initial_input": {"provider": "manual_text"},
            }
        )
    )
    (repo / ".cafe" / "phases.yaml").write_text(
        yaml.safe_dump(
            {
                p: {
                    "name": "Richard" if p == "review" else "David",
                    "clis": [{"cli": "codex", "model": "test-model"}],
                }
                for p in ("diagnose", "develop", "review", "pr")
            }
        )
    )
    playbook = PlaybookLoader(project_root=repo).load("bug", strict=True)
    manager = DefectAgent(repo, issue_dir)
    loader = SkillLoader(project_root=repo)
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name="defect",
        playbook=playbook,
        generic_phase=GenericPhase(
            loader,
            skill_bridge=NativeSkillBridge(loader, project_root=repo, home_dir=tmp_path / "home"),
        ),
        agent_manager=manager,
        git_ops=GitOperations(str(repo)),
        role_agent_map={"developer": "David", "reviewer": "Richard"},
        step_user_inputs={"diagnose": "Fix twice(3): expected 6, observed 5."},
    )

    def execute(step_name, step_def, state, **kwargs):
        if step_name == "diagnose":
            # Replay the same author input on same-iteration retries; never invent scope.
            saved_inputs = sorted((issue_dir / "diagnose").glob("iteration_*/user_input.md"))
            saved = saved_inputs[-1].read_text().strip() if saved_inputs else ""
            executor.step_user_inputs["diagnose"] = saved or "Fix twice(3): expected 6, observed 5."
        return executor.execute_step(step_name, step_def, state, **kwargs)

    def run(start=None):
        runtime = BlackboardWorkflowRuntime(
            issue_dir=issue_dir,
            playbook=playbook,
            executor=execute,
        )
        return runtime.run(start_step=start, max_transitions=30)

    return SimpleNamespace(
        repo=repo,
        issue_dir=issue_dir,
        playbook=playbook,
        agent=manager,
        run=run,
        executor=executor,
    )


def _answer(journey, task, **payload):
    board = BlackboardStore(journey.issue_dir).load_or_create("diagnose", playbook_id="bug")
    result = apply_human_task_payload(
        issue_dir=journey.issue_dir,
        playbook_data=journey.playbook,
        blackboard=board,
        from_step=task.step,
        trigger=task.trigger,
        raw_payload={"task": task.policy_id, "human_task_id": task.id, **payload},
        source="test.authorized_human",
    )
    assert result.target == task.continuations.get(
        payload.get("decision", "submit"), task.step
    ).replace("_done", "done")


def _pending(journey):
    return next(
        t
        for t in HumanTaskRecordStore(journey.issue_dir).tasks()
        if t.status is HumanTaskStatus.PENDING
    )


def test_demonstrated_defect_reaches_distinct_review_and_human_pr(journey):
    """I2/I7: RED/GREEN identity crosses real artifacts; draft cannot finish."""
    result = journey.run("diagnose")
    assert not result.completed
    assert [p for p, _ in journey.agent.calls] == ["diagnose", "develop", "review", "pr"]
    assert journey.agent.calls[2][1] == "Richard"
    assert journey.agent.red.returncode == 1 and journey.agent.green.returncode == 0
    assert _git(journey.repo, "status", "--porcelain") == ""
    task = _pending(journey)
    assert task.policy_id == "delivery-review" and task.step == "pr"
    _answer(journey, task, decision="review_only")
    assert BlackboardStore(journey.issue_dir).load_or_create("diagnose").current_step == "pr"


@pytest.mark.parametrize("failure", ["passes", "setup", "unrelated", "disputed"])
def test_inconclusive_regression_hands_known_facts_to_user(journey, failure):
    """I3: representative inconclusive runs never enter speculative repair."""
    source = {
        "passes": "assert True\n",
        "setup": "import unavailable_bug_fixture\n",
        "unrelated": "assert 2 + 2 == 5\n",
        "disputed": "assert True\n",
    }[failure]
    observed = _process(journey.repo, sys.executable, "-c", source)
    journey.agent.inconclusive = (failure, observed)
    journey.agent.actions[("diagnose", 1)] = {"intent": "need_clarification"}
    result = journey.run("diagnose")
    assert not result.completed
    assert [p for p, _ in journey.agent.calls] == ["diagnose"]
    assert _pending(journey).policy_id == "clarification-feedback"
    output = journey.issue_dir / "diagnose" / "iteration_001" / "output.md"
    assert failure in output.read_text()
    assert "No completed RED proof" in output.read_text()


@pytest.mark.parametrize(
    "origin,target",
    [
        ("review", "develop"),
        ("review", "diagnose"),
        ("develop", "diagnose"),
    ],
)
def test_correction_reaches_causal_target_then_independent_review(journey, origin, target):
    """I4: ordinary repair and invalid diagnosis have distinct production routes."""
    journey.agent.actions[(origin, 1)] = {"intent": "manual_handoff", "target": target}
    journey.run("diagnose")
    phases = [p for p, _ in journey.agent.calls]
    index = phases.index(origin)
    assert phases[index + 1] == target
    assert phases[-2:] == ["review", "pr"]
    correction_inputs = journey.agent.inputs[index + 1][1]
    assert ("BUGD-001" if origin == "develop" else "BUGR-001") in correction_inputs[
        "causal_todo_file"
    ]
    assert journey.agent.identity in correction_inputs.get("prior_output_file", "")


@pytest.mark.parametrize("phase", ["diagnose", "develop", "review"])
def test_fourth_unfinished_attempt_is_blocked_and_authorized_resume_retains_scope(journey, phase):
    """I5: real counters pause before execution; supported adjustment allows resume."""
    # Diagnose can correct itself; repair revisits diagnosis; review revisits repair.
    target = {"diagnose": "diagnose", "develop": "diagnose", "review": "develop"}[phase]
    for visit in range(1, 4):
        journey.agent.actions[(phase, visit)] = {"intent": "manual_handoff", "target": target}
    result = journey.run("diagnose")
    assert not result.completed
    assert sum(p == phase for p, _ in journey.agent.calls) == 3
    task = _pending(journey)
    assert task.policy_id == "iteration-limit" and task.step == phase
    journey.playbook["steps"][phase]["max_attempts_per_cycle"] = 4
    _answer(journey, task, decision="resume")
    journey.run()
    assert sum(p == phase for p, _ in journey.agent.calls) == 4
    assert _pending(journey).policy_id == "delivery-review"


@pytest.mark.parametrize("phase", ["diagnose", "develop", "review"])
@pytest.mark.parametrize("intent", ["need_clarification", "need_permission"])
def test_human_response_resumes_affected_work_with_original_evidence(journey, phase, intent):
    """I6: responses use real HumanTask services and do not widen scope."""
    journey.agent.actions[(phase, 1)] = {"intent": intent}
    journey.run("diagnose")
    task = _pending(journey)
    assert task.step == phase
    _answer(journey, task, feedback="Continue the same bounded defect under existing access.")
    journey.run()
    phases = [p for p, _ in journey.agent.calls]
    first = phases.index(phase)
    assert phases[first + 1] == phase
    resumed = journey.agent.inputs[first + 1]
    assert "bounded defect" in resumed[2]
    assert journey.agent.identity in resumed[1].get(
        "prior_output_file",
        resumed[1].get(
            "previous_output_file",
            resumed[1].get("checkpoint_output_file", resumed[1].get("develop_file", "")),
        ),
    )
    assert _pending(journey).policy_id == "delivery-review"


@pytest.mark.parametrize("feedback_kind", ["local", "mixed"])
def test_pr_feedback_is_curated_before_repair_and_review(journey, feedback_kind):
    """I7: a real local-review corrective batch re-enters PR then code review."""
    journey.run("diagnose")
    _answer(journey, _pending(journey), decision="fix_now", feedback="Retain the exact regression.")
    if feedback_kind in ("github", "mixed"):
        WorkflowFeedbackLedger(journey.issue_dir).record(
            source_identity="github-pr:example/defect:comment:123",
            source_kind="github_pr",
            target_step="pr",
            content="Preserve the bounded repair and unchanged regression.",
        )
    journey.agent.actions[("pr", 2)] = {"intent": "manual_handoff", "target": "develop"}
    journey.run()
    assert [p for p, _ in journey.agent.calls][-4:] == ["pr", "develop", "review", "pr"]
    assert "WF-" in journey.agent.inputs[-3][1]["causal_todo_file"]
    if feedback_kind in ("github", "mixed"):
        assert "PRC-" in journey.agent.inputs[-3][1]["causal_todo_file"]
    assert _pending(journey).policy_id == "delivery-review"


def test_publication_permission_approves_only_exact_request_then_returns_to_local_review(
    journey,
    tmp_path,
    monkeypatch,
):
    """I6/I7: real host policy/approval service retains PR permission and review."""
    import cafe.core.capabilities as capabilities

    definitions = tmp_path / "host-capabilities"
    definitions.mkdir()
    manifest = yaml.safe_load(
        (
            Path(capabilities.__file__).parents[1] / "data/capabilities/cafe.pr.publish.yaml"
        ).read_text()
    )
    manifest["approval"] = "required"
    (definitions / "cafe.pr.publish.yaml").write_text(yaml.safe_dump(manifest))
    # Isolated host policy requires approval; evaluation and correlation remain real.
    monkeypatch.setattr(
        "cafe.core.capabilities.default_capability_definition_dirs", lambda _r: [definitions]
    )
    published = []

    def publish(**_kwargs):
        published.append("published")
        return {
            "pr_url": "https://github.com/example/defect/pull/1",
            "pr_number": "1",
            "action": "created",
        }, None

    monkeypatch.setattr(
        capabilities,
        "HOST_CAPABILITY_ADAPTERS",
        {**capabilities.HOST_CAPABILITY_ADAPTERS, "sync_pr": publish},
    )
    config = yaml.safe_load((journey.issue_dir / "issue.yaml").read_text())
    config["pr"]["auto_create"] = True
    (journey.issue_dir / "issue.yaml").write_text(yaml.safe_dump(config))
    journey.run("diagnose")
    task = _pending(journey)
    assert task.policy_id == "capability-approval" and task.step == "pr"
    assert published == []
    state = BlackboardStore(journey.issue_dir).load_or_create("diagnose")
    service = CapabilityApprovalService(
        issue_dir=journey.issue_dir,
        workflow_id=state.workflow_id,
        step="pr",
        iteration=task.iteration,
    )
    approval = service.inspect(task.id)
    apply_capability_approval_payload(
        issue_dir=journey.issue_dir,
        blackboard=state,
        task=task,
        raw_payload={
            "decision": "approve",
            "workflow_id": state.workflow_id,
            "task_id": task.id,
            "request_fingerprint": approval["fingerprint"],
            "correlation_id": approval["correlation_id"],
        },
    )
    # Replay the approved host request, not a newly drafted artifact/request.
    directory = journey.issue_dir / "pr" / f"iteration_{task.iteration:03d}"

    def resume_publication(step_name, step_def, board, **_kwargs):
        assert step_name == "pr"
        (journey.issue_dir / "next_step.txt").write_text(
            json.dumps(
                {
                    "version": 1,
                    "to_owner": "user",
                    "to_step": "user",
                    "intent": "confirm_output",
                }
            )
        )
        result = GitHubPRCreator().run(
            stage="publish_output",
            phase=journey.executor,
            step_name=step_name,
            step_def=step_def,
            output_file=directory / "output.md",
            capability_request_file=directory / "capability_request.json",
            blackboard_state=board,
            status_code="confirmed",
        )
        return StepExecutionResult(
            response="confirmed",
            status_code="confirmed",
            artifacts={"pr_result": str(directory / "output.md")},
            events=result.events,
        )

    resumed = BlackboardWorkflowRuntime(
        issue_dir=journey.issue_dir, playbook=journey.playbook, executor=resume_publication
    ).run(start_step="pr")
    assert not resumed.completed
    assert published == ["published"], (resumed.final_status_code, resumed.detail)
    assert _pending(journey).policy_id == "delivery-review"


@pytest.mark.parametrize("phase", ["before_red", "diagnose", "develop", "review"])
def test_interrupted_proof_resumes_with_prior_output_and_cannot_skip_review(journey, phase):
    """I8: durable partial evidence and real interruption retry retain identity."""
    journey.agent.interrupt_at = phase
    result = journey.run("diagnose")
    assert not result.completed
    task = _pending(journey)
    affected = "diagnose" if phase == "before_red" else phase
    assert task.policy_id == "agent-execution-interrupted" and task.step == affected
    _answer(journey, task, decision="retry")
    journey.run()
    assert [p for p, _ in journey.agent.calls][-2:] == ["review", "pr"]
    resumed = [inputs for p, inputs, _ in journey.agent.inputs if p == affected][-1]
    previous = resumed.get(
        "prior_output_file",
        resumed.get("previous_output_file", resumed.get("checkpoint_output_file", "")),
    )
    assert ("RED proof unfinished" if phase == "before_red" else journey.agent.identity) in previous
    assert _pending(journey).policy_id == "delivery-review"


def test_missing_delivery_details_pause_for_clarification_without_corrupting_todo(journey):
    from cafe.core.todo import parse_todo_list

    journey.agent.delivery_details_missing = True
    result = journey.run("diagnose")
    assert not result.completed
    task = _pending(journey)
    assert task.policy_id == "delivery-details" and task.step == "pr"
    output = journey.issue_dir / "pr/iteration_001/output.md"
    assert parse_todo_list(output.read_text()) == ()
    assert "## Delivery action details required" in output.read_text()
