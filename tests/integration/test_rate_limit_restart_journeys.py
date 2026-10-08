"""I1–I6: persisted settings → HumanTask → generic step → real streamed child.

Only provider process launch and retry sleep are doubled. Internal policy,
records, workflow, sessions, chain selection, previews and stream parsing run
through production paths with arbitrary workflow/step/role names.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest
import yaml

from cafe.agents.executor import AgentExecutor
from cafe.agents.manager import AgentManager
from cafe.core.blackboard import BlackboardStore
from cafe.core.git import GitOperations
from cafe.core.human_task_records import HumanTaskRecordStore, HumanTaskStatus
from cafe.core.types import AgentCLI, AgentConfig, CliEntry
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
from cafe.phases.generic_phase import GenericPhase
from cafe.phases.generic_workflow_step import GenericWorkflowStepExecutor
from cafe.settings import SettingUpdateRequest, dispatch_setting_update
from cafe.skills.loader import SkillLoader
from cafe.skills.native_bridge import NativeSkillBridge
from cafe.ui.human_tasks import apply_human_task_payload

PROVIDER = r"""
import json, sys
from pathlib import Path
issue, cli, session, mode_path = sys.argv[1:]
mode = json.loads(Path(mode_path).read_text())
session = session or ('new-' + cli)
if cli == 'codex':
    print(json.dumps({'type': 'thread.started', 'thread_id': session}), flush=True)
else:
    print(json.dumps({'type': 'init', 'session_id': session}), flush=True)
if mode.get(cli) in {'limited', 'broken'}:
    error = 'Rate limit exceeded; code: 429' if mode[cli] == 'limited' else 'Provider failed'
    print(error, file=sys.stderr, flush=True)
    sys.exit(1)
directory = max((Path(issue) / 'compose').glob('iteration_*'))
if mode.get('write', True):
    (directory / 'output.md').write_text('Completed the authorized work.\n')
    (directory / 'checklist.md').write_text('[x] Complete the requested work\n')
    intent = 'invalid' if mode.pop('bad_baton_once', False) else 'workflow_complete'
    Path(mode_path).write_text(json.dumps(mode))
    baton = {'version': 1, 'to_owner': 'done', 'to_step': 'done', 'intent': intent}
    (Path(issue) / 'next_step.txt').write_text(json.dumps(baton))
if cli == 'codex':
    item = {'type': 'agent_message', 'text': 'Completed'}
    print(json.dumps({'type': 'item.completed', 'item': item}), flush=True)
    if mode.get('terminal', True):
        usage = {'input_tokens': 1, 'output_tokens': 1}
        print(json.dumps({'type': 'turn.completed', 'usage': usage}), flush=True)
else:
    print(json.dumps({'type': 'message', 'role': 'assistant', 'content': 'Completed'}), flush=True)
    if mode.get('terminal', True):
        print(json.dumps({'type': 'result', 'status': 'success'}), flush=True)
"""


class Journey:
    def __init__(self, tmp_path, monkeypatch, *, policy=None, single=False):
        monkeypatch.chdir(tmp_path)
        subprocess.run(["git", "init", "-q"], check=True)
        subprocess.run(["git", "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], check=True)
        (tmp_path / ".gitignore").write_text(".cafe/\n.codex/\n.gemini/\n")
        subprocess.run(["git", "add", ".gitignore"], check=True)
        subprocess.run(["git", "commit", "-qm", "Initial test repository"], check=True)
        self.issue = tmp_path / ".cafe" / "issues" / "example"
        self.issue.mkdir(parents=True)
        self.config = self.issue / "issue.yaml"
        self.config.write_text(
            yaml.safe_dump(
                {
                    "playbook": "custom",
                    **({"execution": {"rate_limit_restart_policy": policy}} if policy else {}),
                }
            )
        )
        self.graph = {
            "playbook": {"id": "custom"},
            "roles": {"author": {"default_agent": "Writer"}},
            "steps": {
                "compose": {
                    "role": "author",
                    "skill": "compose",
                    "behavior": {"completion": "baton"},
                    "on": {"workflow_complete": "_done"},
                }
            },
        }
        skill = tmp_path / ".cafe" / "skills" / "compose"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: compose\ndescription: Compose work\n---\n"
            "Write result to {output_file} and baton to {next_step_path}.\n"
        )
        agent = tmp_path / ".cafe" / "agents" / "author" / "Writer.md"
        agent.parent.mkdir(parents=True)
        agent.write_text(
            "---\nname: Writer\ndescription: Test author\n---\nComplete the authorized work.\n"
        )
        self.entries = [("codex", "primary-model")] + (
            [] if single else [("gemini", "fallback-model")]
        )
        self.phases = tmp_path / ".cafe" / "phases.yaml"
        self.save_chain(self.entries)
        loader = SkillLoader(
            project_root=tmp_path,
            global_root=tmp_path / "global",
            builtin_root=tmp_path / "builtin",
        )
        loader.discover()
        self.generic = GenericPhase(
            loader,
            skill_bridge=NativeSkillBridge(
                loader, project_root=tmp_path, home_dir=tmp_path / "home"
            ),
        )
        self.manager = AgentManager(issue_name="example")
        self.register_chain()
        if not single:
            (self.issue / "active_clis.json").write_text(
                json.dumps(
                    {
                        "Writer": {
                            "cli": "gemini",
                            "model": "fallback-model",
                            "configured_primary": "codex",
                            "chain": [{"cli": c, "model": m} for c, m in self.entries],
                        }
                    }
                )
            )
        self.mode = tmp_path / "provider-mode.json"
        self.mode.write_text(json.dumps({"codex": "limited", "gemini": "limited"}))
        script = tmp_path / "provider.py"
        script.write_text(PROVIDER)
        self.attempts = []
        self.on_provider_launch = None
        self.children = []
        self.commands = []
        self.previews = []
        self.environment_previews = []
        environment = AgentExecutor.preview_cli_environment

        def preview_environment(executor):
            self.environment_previews.append(executor.config.cli.value)
            return environment(executor)

        monkeypatch.setattr(AgentExecutor, "preview_cli_environment", preview_environment)
        launch = subprocess.Popen

        def popen(cmd, *args, **kwargs):
            if cmd and cmd[0] in ("codex", "gemini"):
                cli = cmd[0]
                session = (
                    cmd[cmd.index("resume") + 1]
                    if "resume" in cmd
                    else cmd[cmd.index("--resume") + 1] if "--resume" in cmd else None
                )
                model = cmd[cmd.index("--model") + 1] if "--model" in cmd else None
                if not self.attempts:
                    context_path = self.issue / "compose" / "iteration_001" / "iteration.json"
                    self.previews.append(json.loads(context_path.read_text())["cli_command_args"])
                self.attempts.append((cli, model, session))
                self.commands.append(list(cmd))
                if self.on_provider_launch:
                    self.on_provider_launch(cli)
                process = launch(
                    [
                        sys.executable,
                        str(script),
                        str(self.issue),
                        cli,
                        session or "",
                        str(self.mode),
                    ],
                    *args,
                    **kwargs,
                )
                self.children.append(process)
                return process
            return launch(cmd, *args, **kwargs)

        monkeypatch.setattr("subprocess.Popen", popen)
        monkeypatch.setattr("cafe.agents.manager.time.sleep", lambda _delay: None)
        self.tmp_path = tmp_path
        self.reload()

    def save_chain(self, entries):
        self.entries = entries
        self.phases.write_text(
            yaml.safe_dump(
                {
                    "compose": {
                        "name": "Writer",
                        "role": "author",
                        "clis": [{"cli": c, "model": m} for c, m in entries],
                    }
                }
            )
        )

    def register_chain(self):
        self.manager.register_agent(
            AgentConfig(
                name="Writer",
                cli=AgentCLI(self.entries[0][0]),
                clis=[CliEntry(cli=AgentCLI(c), model=m) for c, m in self.entries],
            )
        )

    def reload(self):
        self.executor = GenericWorkflowStepExecutor(
            issue_dir=self.issue,
            issue_name="example",
            playbook=self.graph,
            generic_phase=self.generic,
            agent_manager=self.manager,
            git_ops=GitOperations(self.tmp_path),
            role_agent_map={"author": "Writer"},
        )
        self.runtime = BlackboardWorkflowRuntime(
            issue_dir=self.issue, playbook=self.graph, executor=self.executor.execute_step
        )

    def interrupt(self):
        result = self.runtime.run(start_step="compose")
        assert not result.completed and result.final_status_code == "INTERRUPTED:agent_rate_limit"
        return next(
            t
            for t in HumanTaskRecordStore(self.issue).tasks()
            if t.status is HumanTaskStatus.PENDING and t.step == "compose"
        )

    def answer(self, task, decision):
        boards = BlackboardStore(self.issue)
        state = boards.load_or_create("compose", playbook_id="custom")
        return apply_human_task_payload(
            issue_dir=self.issue,
            playbook_data=self.graph,
            blackboard=state,
            from_step="compose",
            trigger="agent_execution_interrupted",
            raw_payload={"human_task_id": task.id, "decision": decision},
            source="test",
        )

    def restart(self, task, decision, mode):
        answer = self.answer(task, decision)
        assert answer.rejection is None
        self.mode.write_text(json.dumps(mode))
        self.attempts.clear()
        self.previews.clear()
        self.environment_previews.clear()
        self.reload()
        return self.runtime.run()

    def data(self):
        return json.loads((self.issue / "compose" / "iteration_001" / "iteration.json").read_text())


def test_default_restart_preserves_fallback_session_and_inspectable_policy(tmp_path, monkeypatch):
    """I1/U5: absent policy retains exact interrupted fallback when explicitly selected."""
    journey = Journey(tmp_path, monkeypatch)
    task = journey.interrupt()
    prior = journey.data()
    result = journey.restart(task, "retry", {})
    assert result.completed
    assert journey.attempts[0] == (prior["cli"], prior["model"], prior["session_id"])
    assert journey.data()["restart_diagnostics"]["saved_policy"] == "continue_last_success"
    assert (
        journey.data()["restart_diagnostics"]["override_reason"] == "user_selected_existing_session"
    )


def test_setting_roundtrip_primary_recovered_and_terminal_exit_proof(tmp_path, monkeypatch):
    """I2/U7/U8: public settings and human decision reach real previews/execution."""
    journey = Journey(tmp_path, monkeypatch)
    preview = dispatch_setting_update(
        "execution.rate_limit_restart_policy",
        SettingUpdateRequest(journey.config, "recheck_priority", True),
    )
    assert preview.status == "proposed" and "execution" not in yaml.safe_load(
        journey.config.read_text()
    )
    dispatch_setting_update(
        "execution.rate_limit_restart_policy",
        SettingUpdateRequest(journey.config, "recheck_priority"),
    )
    task = journey.interrupt()
    result = journey.restart(task, "retry_configured_order", {})
    assert result.completed
    assert journey.attempts == [("codex", "primary-model", None)]
    data = journey.data()
    diagnostics = data["restart_diagnostics"]
    assert diagnostics["effective_policy"] == "recheck_priority"
    assert diagnostics["human_task_id"] == task.id
    assert (
        diagnostics["configured_order"]
        == diagnostics["effective_order"]
        == [{"cli": c, "model": m} for c, m in journey.entries]
    )
    assert diagnostics["sticky_disposition"] == "bypassed"
    assert "exec" in journey.previews[0] and "resume" not in journey.previews[0]
    assert journey.environment_previews == ["codex"]
    assert all(child.poll() is not None for child in journey.children)
    assert journey.children[-1].returncode == 0
    assert data["end_time"] and data.get("workflow_completion_trusted") is not False
    assert journey.runtime.blackboard.current_step == "done"


def test_unrecovered_primary_uses_existing_retries_then_fallback_and_exact_correction(
    tmp_path, monkeypatch
):
    """I3/I6: configured primary retries precede fallback; later correction resumes success."""
    journey = Journey(tmp_path, monkeypatch, policy="recheck_priority")
    task = journey.interrupt()
    result = journey.restart(task, "retry_configured_order", {"codex": "limited"})
    assert result.completed
    assert [a[0] for a in journey.attempts] == ["codex", "codex", "codex", "gemini"]
    assert journey.data()["cli"] == "gemini"
    assert len(journey.data()["failed_attempts"]) == 3
    sticky = json.loads((journey.issue / "active_clis.json").read_text())["Writer"]
    assert sticky["chain"] == [{"cli": c, "model": m} for c, m in journey.entries]
    journey.executor.iteration = 2
    continuation = journey.executor._select_session_continuation(
        agent_name="Writer",
        step_def={},
        workflow_id=journey.runtime.blackboard.workflow_id,
        same_invocation_retry=True,
    )
    assert continuation.is_exact and continuation.cli is AgentCLI.GEMINI


@pytest.mark.parametrize("decision", ["retry", "retry_fresh_session", "retry_configured_order"])
def test_pending_upgrade_retains_human_override_and_rejects_old_task(
    tmp_path, monkeypatch, decision
):
    """I4: a settings-only save cannot execute; replacement exposes declared decisions."""
    journey = Journey(tmp_path, monkeypatch)
    old = journey.interrupt()
    prior = journey.data()
    dispatch_setting_update(
        "execution.rate_limit_restart_policy",
        SettingUpdateRequest(journey.config, "recheck_priority"),
    )
    count = len(journey.attempts)
    journey.reload()
    result = journey.runtime.run()
    assert not result.completed and len(journey.attempts) == count
    records = HumanTaskRecordStore(journey.issue)
    assert records.get_task(old.id).status is HumanTaskStatus.CANCELLED
    assert "retry_configured_order" not in records.get_task(old.id).continuations
    task = next(t for t in records.tasks() if t.status is HumanTaskStatus.PENDING)
    assert journey.answer(old, "retry_configured_order").rejection is not None
    result = journey.restart(task, decision, {})
    assert result.completed
    if decision == "retry":
        assert journey.attempts[0] == (prior["cli"], prior["model"], prior["session_id"])
    elif decision == "retry_configured_order":
        assert journey.attempts[0] == ("codex", "primary-model", None)
    else:
        assert journey.attempts[0][2] is None
    assert (
        yaml.safe_load(journey.config.read_text())["execution"]["rate_limit_restart_policy"]
        == "recheck_priority"
    )


@pytest.mark.parametrize("policy", ["continue_last_success", "recheck_priority"])
@pytest.mark.parametrize(
    "entries",
    [
        [("codex", "new-model")],
        [("gemini", "new-primary"), ("codex", "changed")],
        [("codex", "primary-model"), ("gemini", "new-fallback")],
    ],
)
def test_configuration_change_uses_current_entries_and_models(
    tmp_path, monkeypatch, policy, entries
):
    """I5: changes before authorized restart invalidate stale fallback/model snapshots."""
    journey = Journey(tmp_path, monkeypatch, policy=policy)
    task = journey.interrupt()
    journey.save_chain(entries)
    journey.register_chain()
    decision = "retry_configured_order" if policy == "recheck_priority" else "retry_fresh_session"
    result = journey.restart(task, decision, {})
    assert result.completed
    assert journey.attempts[0] == (*entries[0], None)
    assert journey.data()["restart_diagnostics"]["sticky_disposition"] == "stale"


def test_single_entry_replay_does_not_invent_backup_or_automatic_recovery(tmp_path, monkeypatch):
    """I6: consumed result cannot authorize a new unrelated interruption."""
    journey = Journey(tmp_path, monkeypatch, policy="recheck_priority", single=True)
    task = journey.interrupt()
    result = journey.restart(task, "retry_configured_order", {"codex": "limited"})
    assert not result.completed
    assert [a[0] for a in journey.attempts] == ["codex"] * 3
    marker = journey.data()["restart_recovery_consumption"]
    journey.reload()
    count = len(journey.attempts)
    assert not journey.runtime.run().completed
    assert len(journey.attempts) == count
    assert journey.data()["restart_recovery_consumption"] == marker
    assert all(child.poll() is not None and child.returncode == 1 for child in journey.children)


def test_clean_process_exit_without_terminal_cannot_complete_restart(tmp_path, monkeypatch):
    """I6: explicit structured terminal and child exit remain separate proof."""
    journey = Journey(tmp_path, monkeypatch, policy="recheck_priority", single=True)
    task = journey.interrupt()
    result = journey.restart(task, "retry_configured_order", {"terminal": False, "write": False})
    assert not result.completed
    assert journey.children[-1].returncode == 0
    assert journey.data().get("workflow_completion_trusted") is not True


def test_restart_diagnostics_redact_credentials_without_inventing_success(tmp_path, monkeypatch):
    """U7/I5: exposed diagnostics stay safe and actual success stays separate."""
    journey = Journey(tmp_path, monkeypatch, policy="recheck_priority", single=True)
    task = journey.interrupt()
    journey.save_chain([("codex", "primary token=private")])
    journey.register_chain()
    result = journey.restart(task, "retry_configured_order", {})
    assert result.completed
    data = journey.data()
    assert "private" not in json.dumps(data["restart_diagnostics"])
    assert data["restart_diagnostics"]["configured_order"][0]["model"] != data["model"]
    assert data["cli"] == "codex" and data["model"] == "primary token=private"


def test_human_cli_change_does_not_admit_changed_constraint_definitions(tmp_path, monkeypatch):
    """U7/I6: correlated recovery admits CLI context changes, never changed limits."""
    journey = Journey(tmp_path, monkeypatch, policy="recheck_priority")
    task = journey.interrupt()
    path = journey.issue / "compose" / "iteration_001" / "iteration.json"
    data = journey.data()
    data["runtime_constraints"]["digest"] = (
        "0" * 64
    )  # A stored digest from different constraint semantics.
    path.write_text(json.dumps(data))
    result = journey.restart(task, "retry_configured_order", {})
    assert (
        not result.completed and result.final_status_code == "INTERRUPTED:agent_constraints_changed"
    )
    assert journey.attempts == []
    assert "restart_recovery_consumption" not in journey.data()


def test_public_baton_correction_keeps_fallback_session_and_consumed_recovery(
    tmp_path, monkeypatch
):
    """I3/I6: real runtime correction cannot repeat the priority reset."""
    journey = Journey(tmp_path, monkeypatch, policy="recheck_priority")
    task = journey.interrupt()
    result = journey.restart(
        task, "retry_configured_order", {"codex": "limited", "bad_baton_once": True}
    )
    assert result.completed
    assert [a[0] for a in journey.attempts] == ["codex"] * 3 + ["gemini", "gemini"]
    assert journey.attempts[-1][2] == "new-gemini"
    assert journey.data()["restart_recovery_consumption"]["human_task_id"] == task.id


def test_completed_restart_replay_and_saved_handoff_do_not_reexecute(tmp_path, monkeypatch):
    """I6: a completed correlated result and valid handoff remain complete."""
    journey = Journey(tmp_path, monkeypatch, policy="recheck_priority", single=True)
    task = journey.interrupt()
    assert journey.restart(task, "retry_configured_order", {}).completed
    marker = journey.data()["restart_recovery_consumption"]
    count = len(journey.attempts)
    journey.answer(task, "retry_configured_order")
    journey.reload()
    assert journey.runtime.run().completed
    assert len(journey.attempts) == count
    assert journey.data()["restart_recovery_consumption"] == marker


def test_subsequent_unrelated_interruption_needs_its_own_human_task(tmp_path, monkeypatch):
    """I6/U2: the consumed reset cannot qualify a later provider error."""
    journey = Journey(tmp_path, monkeypatch, policy="recheck_priority", single=True)
    task = journey.interrupt()
    result = journey.restart(task, "retry_configured_order", {"codex": "broken"})
    assert not result.completed and result.final_status_code == "INTERRUPTED:agent_error"
    pending = next(
        t
        for t in HumanTaskRecordStore(journey.issue).tasks()
        if t.status is HumanTaskStatus.PENDING
    )
    assert pending.id != task.id and "retry_configured_order" not in pending.continuations
    assert journey.answer(task, "retry_configured_order").rejection is not None


def test_configuration_change_during_invocation_waits_until_next_invocation(tmp_path, monkeypatch):
    """I5/U7: active fallback/preview/canonical recording use the one resolved snapshot."""
    journey = Journey(tmp_path, monkeypatch, policy="recheck_priority")
    task = journey.interrupt()

    def change_config(_cli):
        journey.on_provider_launch = None
        journey.save_chain([("gemini", "different-primary")])
        journey.register_chain()

    journey.on_provider_launch = change_config
    result = journey.restart(task, "retry_configured_order", {"codex": "limited"})
    assert result.completed
    assert journey.attempts[-1] == ("gemini", "fallback-model", None)
    sticky = json.loads((journey.issue / "active_clis.json").read_text())["Writer"]
    assert sticky["configured_primary"] == "codex"
    assert sticky["chain"] == [
        {"cli": "codex", "model": "primary-model"},
        {"cli": "gemini", "model": "fallback-model"},
    ]


def test_cli_settings_and_task_completion_authorize_current_primary(tmp_path, monkeypatch):
    """I2/U8/U6: installed settings/task commands forward the declared recovery."""
    from typer.testing import CliRunner

    from cafe.ui.cli import app

    journey = Journey(tmp_path, monkeypatch, single=True)
    playbook_path = tmp_path / ".cafe" / "playbooks" / "custom.yaml"
    playbook_path.parent.mkdir(parents=True)
    playbook_path.write_text(yaml.safe_dump(journey.graph))
    runner = CliRunner()
    command = [
        "settings",
        "update",
        "example",
        "--set",
        'execution.rate_limit_restart_policy="recheck_priority"',
        "--json",
    ]
    preview = runner.invoke(app, [*command, "--preview"])
    assert preview.exit_code == 0, preview.stdout
    assert json.loads(preview.stdout)["status"] == "proposed"
    saved = runner.invoke(app, command)
    assert saved.exit_code == 0, saved.stdout
    task = journey.interrupt()
    completed = runner.invoke(
        app,
        [
            "task",
            "complete",
            task.id,
            "--result",
            '{"decision":"retry_configured_order"}',
            "--no-resume",
            "--json",
        ],
    )
    assert completed.exit_code == 0, completed.stdout
    journey.mode.write_text("{}")
    journey.attempts.clear()
    journey.reload()
    assert journey.runtime.run().completed
    assert journey.attempts == [("codex", "primary-model", None)]


def test_preparation_failure_preserves_unconsumed_authorization_for_explicit_retry(
    tmp_path, monkeypatch
):
    """U7/I6: failed preparation cannot consume or replace the rate-limit receipt."""
    journey = Journey(tmp_path, monkeypatch, policy="recheck_priority", single=True)
    task = journey.interrupt()
    original = journey.data()["agent_interruption"]
    prepare = journey.generic.prepare_skill

    def unavailable(**_kwargs):
        raise OSError("temporary skill installation failure")

    monkeypatch.setattr(journey.generic, "prepare_skill", unavailable)
    result = journey.restart(task, "retry_configured_order", {})
    assert not result.completed and journey.attempts == []
    data = journey.data()
    assert "restart_recovery_consumption" not in data
    assert data["agent_interruption"] == original
    assert data["restart_preparation_failure"]["reason"] == "agent_error"
    monkeypatch.setattr(journey.generic, "prepare_skill", prepare)
    journey.reload()
    # Explicit start uses the existing authorized start-step recovery route;
    # ordinary run while waiting never auto-executes the preserved decision.
    assert not journey.runtime.run().completed and journey.attempts == []
    assert journey.runtime.run(start_step="compose").completed
    assert journey.attempts == [("codex", "primary-model", None)]
    marker = journey.data()["restart_recovery_consumption"]
    assert marker["human_task_id"] == task.id
    assert marker["result_id"] == HumanTaskRecordStore(journey.issue).get_result(task.id).id


def test_public_correction_preserves_models_when_configuration_changes_mid_invocation(
    tmp_path, monkeypatch
):
    """R4/U7/I5/I6: correction shares the original order/models/canonical snapshot."""
    journey = Journey(tmp_path, monkeypatch, policy="recheck_priority")
    task = journey.interrupt()

    def change_config(_cli):
        journey.on_provider_launch = None
        journey.save_chain([("codex", "different-primary"), ("gemini", "different-fallback")])
        journey.register_chain()

    journey.on_provider_launch = change_config
    result = journey.restart(
        task, "retry_configured_order", {"codex": "limited", "bad_baton_once": True}
    )
    assert result.completed
    assert journey.attempts == [
        ("codex", "primary-model", None),
        ("codex", "primary-model", "new-codex"),
        ("codex", "primary-model", "new-codex"),
        ("gemini", "fallback-model", None),
        ("gemini", "fallback-model", "new-gemini"),
    ]
    sticky = json.loads((journey.issue / "active_clis.json").read_text())["Writer"]
    assert sticky["configured_primary"] == "codex"
    assert sticky["chain"] == [
        {"cli": "codex", "model": "primary-model"},
        {"cli": "gemini", "model": "fallback-model"},
    ]
