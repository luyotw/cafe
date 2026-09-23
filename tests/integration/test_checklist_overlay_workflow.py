"""Production journeys for independently selected checklist overlays."""

from pathlib import Path

import pytest
import yaml

from cafe.playbooks.loader import PlaybookLoader


def write_skill(root, name, workflow=None, references=None):
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    data = {"name": name, "description": name, "workflow": workflow or {}}
    (directory / "SKILL.md").write_text("---\n" + yaml.safe_dump(data) + "---\n# Policy\n")
    for filename, content in (references or {}).items():
        target = directory / "references" / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return directory


def overlay(reference="review.md", **extra):
    return {"checklist_overlay": {"variants": [{"sections": [{"reference": reference}]}], **extra}}


def test_author_validates_inactive_policy_and_primary_alternatives(tmp_path):
    """I01/U02/U04: strict public loading validates project overrides and all branches."""
    builtin = tmp_path / "builtin"
    project = tmp_path / "project"
    for name in ("first", "later"):
        write_skill(builtin / "skills", name)
    write_skill(builtin / "skills", "policy")
    policy = overlay(when={"feedback": True})
    policy["required_tools"] = ["Write"]
    policy["prompt_inputs"] = [
        {"artifacts": ["notes"], "placeholder": "notes_file", "required": True}
    ]
    directory = write_skill(
        project / ".cafe/skills", "policy", policy, {"review.md": "[ ] Review\n"}
    )
    playbook = {
        "playbook": {
            "id": "overlay",
            "applicability": {"summary": "test", "use_when": ["test"], "avoid_when": ["other"]},
        },
        "roles": {"operator": {}},
        "commands": {"prepare": {"prompt_for_spec_plan_config": False}},
        "skills": {"workflow": {"shared": ["policy"]}, "chat": {"shared": []}},
        "steps": {
            "assemble": {
                "role": "operator",
                "skill": {"1": "first", "default": "later"},
                "input_artifacts": ["notes"],
                "allowed_tools": ["Read", "Write"],
                "on": {"await_agent": "_done"},
            }
        },
    }
    folder = builtin / "playbooks"
    folder.mkdir(parents=True)
    path = folder / "overlay.yaml"
    loader = PlaybookLoader(
        project_root=project, global_root=tmp_path / "global", builtin_root=builtin
    )

    def validate():
        path.write_text(yaml.safe_dump(playbook))
        return loader.load_model("overlay", strict=True)

    validate()
    playbook["steps"]["assemble"]["allowed_tools"] = ["Read"]
    with pytest.raises(ValueError, match="Write"):
        validate()
    playbook["steps"]["assemble"]["allowed_tools"].append("Write")
    playbook["steps"]["assemble"]["input_artifacts"] = []
    with pytest.raises(ValueError, match="notes_file"):
        validate()
    playbook["steps"]["assemble"]["input_artifacts"] = ["notes"]
    (directory / "references/review.md").unlink()
    with pytest.raises(ValueError) as error:
        validate()
    assert all(
        token in str(error.value)
        for token in ("assemble", "policy", "checklist_overlay", "review.md")
    )
    (directory / "references/review.md").write_text("[ ] Review\n")
    write_skill(
        builtin / "skills",
        "later",
        {"checklist": {"variants": [{"sections": [{"reference": "missing.md"}]}]}},
    )
    with pytest.raises(ValueError, match="missing.md"):
        validate()


def executor_fixture(tmp_path, monkeypatch, primary="primary", injections=("policy",), iteration=1):
    from types import SimpleNamespace

    from cafe.core.blackboard import BlackboardStore
    from cafe.phases.generic_phase import GenericPhase
    from cafe.phases.generic_workflow_step import GenericWorkflowStepExecutor
    from cafe.skills.loader import SkillLoader
    from cafe.skills.native_bridge import NativeSkillBridge

    monkeypatch.chdir(tmp_path)
    loader = SkillLoader(
        project_root=tmp_path,
        global_root=tmp_path / "global",
        builtin_root=Path(__file__).resolve().parents[2] / "src/cafe/data",
    )
    phase = GenericPhase(
        loader,
        skill_bridge=NativeSkillBridge(loader, project_root=tmp_path, home_dir=tmp_path / "home"),
    )
    step = {
        "skill": primary,
        "role": "developer",
        "on": {"await_agent": "_done"},
        "allowed_tools": ["Read", "Write"],
    }
    playbook = {
        "playbook": {"id": "overlay"},
        "skills": {"workflow": {"shared": list(injections)}, "chat": {"shared": []}},
        "steps": {"assemble": step},
    }
    issue = tmp_path / ".cafe/issues/example"
    issue.mkdir(parents=True)
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue,
        issue_name="example",
        playbook=playbook,
        generic_phase=phase,
        agent_manager=SimpleNamespace(),
        git_ops=SimpleNamespace(get_default_base_branch=lambda: "main"),
        role_agent_map={"developer": "David"},
    )
    executor.phase_name = "assemble"
    executor.phase_dir = issue / "assemble"
    executor.iteration = iteration
    directory = executor.phase_dir / f"iteration_{iteration:03}"
    directory.mkdir(parents=True)
    state = BlackboardStore(issue).load_or_create("assemble")
    return executor, step, state, directory


def generate(executor, step, state, directory, **kwargs):
    executor._generate_checklist(
        step_name="assemble",
        skill_name=executor._resolve_skill_name(step, executor.iteration),
        agent_name="David",
        step_def=step,
        blackboard_state=state,
        checklist_file=directory / "checklist.md",
        output_file=directory / "output.md",
        questions_xml_file=directory / "questions.xml",
        **kwargs,
    )
    return (directory / "checklist.md").read_text()


@pytest.mark.parametrize("legacy", [False, True])
def test_custom_fallback_appends_overlay_before_single_guidance(tmp_path, monkeypatch, legacy):
    """I03/U08: both empty and legacy fallbacks retain independent overlays."""
    root = tmp_path / ".cafe/skills"
    write_skill(
        root,
        "primary",
        references={"execution_steps_normal.md": "[ ] Legacy work\n"} if legacy else {},
    )
    write_skill(root, "policy", overlay(), {"review.md": "[ ] Independent policy\n"})
    executor, step, state, directory = executor_fixture(tmp_path, monkeypatch)
    content = generate(executor, step, state, directory)
    assert content.count("[ ] Independent policy") == 1
    assert ("[ ] Legacy work" in content) == legacy
    assert content.count("## Agent Guidelines Checklist") <= 1
    if legacy:
        assert (
            content.index("Legacy work")
            < content.index("Independent policy")
            < content.index("## Agent Guidelines Checklist")
        )
    executor._save_user_input("continue")
    import json

    assert json.loads((directory / "iteration.json").read_text())["effective_checklist"]


@pytest.mark.parametrize("feedback", [False, True])
def test_real_develop_materializes_primary_and_independent_policy(tmp_path, monkeypatch, feedback):
    """I02: real Develop metadata retains normal/correction selection with overlays."""
    from cafe.core.blackboard import BlackboardStore

    root = tmp_path / ".cafe/skills"
    policy = {
        "prompt_inputs": [{"artifacts": ["overlay_work"], "placeholder": "overlay_feedback"}],
        "checklist_overlay": {
            "variants": [
                {
                    "when": {"feedback": True},
                    "sections": [
                        {"reference": "correction.md"},
                        {"todo_projection": {"artifact": "overlay_work", "causal": True}},
                    ],
                },
                {"sections": [{"reference": "review.md"}]},
            ]
        },
    }
    write_skill(
        root,
        "policy",
        policy,
        {
            "review.md": "[ ] Independent policy\n",
            "correction.md": "[ ] Independent policy reads {overlay_feedback}\n",
        },
    )
    executor, step, state, directory = executor_fixture(
        tmp_path, monkeypatch, primary="cafe-develop"
    )
    store = BlackboardStore(executor.issue_dir)
    todo = tmp_path / "todo.md"
    todo.write_text(
        "## Todo List\n- [ ] `PLAN-001` — Source: `plan` — Work: implement "
        "— Closure: works — Evidence: tests\n"
    )
    store.set_artifact(state, "plan", str(todo))
    if feedback:
        from hashlib import sha256

        from cafe.core.blackboard import ArtifactEntry, ArtifactKind

        executor.playbook["steps"]["inspect"] = {
            "skill": "cafe-review",
            "role": "reviewer",
            "output_artifact": "findings",
            "allowed_goto": ["assemble"],
            "behavior": {
                "feedback_routes": {
                    "assemble": {
                        "artifact": "findings",
                        "source_kind": "bespoke",
                        "todo_source": "bespoke",
                        "todo_id_prefix": "FIX",
                    }
                }
            },
            "on": {"await_agent": "assemble"},
        }
        import json

        findings = executor.issue_dir / "inspect/iteration_001/output.md"
        findings.parent.mkdir(parents=True)
        findings.write_text(
            "## Todo List\n- [ ] `FIX-001` — Source: `bespoke` — Work: repair "
            "— Closure: correct — Evidence: tests\n"
        )
        state.artifacts["findings"] = ArtifactEntry(
            name="findings",
            kind=ArtifactKind.DOCUMENT,
            version=1,
            updated_by="inspect",
            path=str(findings),
            content_sha256=sha256(findings.read_bytes()).hexdigest(),
        )
        (findings.parent / "artifact.json").write_text(
            json.dumps(state.artifacts["findings"].to_dict())
        )
        store.record_event(
            state,
            "transition",
            {
                "from": "inspect",
                "to": "assemble",
                "source_artifact": state.artifacts["findings"].to_dict(),
            },
        )
        step["input_artifacts"] = ["plan", "findings"]
    context = executor._build_context(
        step_name="assemble",
        step_def=step,
        blackboard_state=state,
        agent_name="David",
        output_file=directory / "output.md",
    )
    content = generate(executor, step, state, directory, runtime_context=context)
    if feedback:
        assert context["overlay_feedback"].endswith("inspect/iteration_001/output.md")
        assert content.count("`FIX-001__") == 2
    assert content.count("[ ] Independent policy") == 1
    assert content.count("## Agent Guidelines Checklist") == 1
    assert content.index("Independent policy") < content.index("## Agent Guidelines Checklist")
    assert ("FIX-001" if feedback else "PLAN-001") in content


@pytest.mark.parametrize(
    "mutation",
    [
        "unchanged",
        "source",
        "content",
        "condition",
        "continuation",
        "missing_metadata",
        "corrupt_metadata",
    ],
)
def test_interrupted_identical_gates_restore_only_proven_source(tmp_path, monkeypatch, mutation):
    """I04/U09/U10: identical text never transfers completion across sources."""
    from cafe.utils.checklist_validator import validate_checklist

    root = tmp_path / ".cafe/skills"
    write_skill(
        root,
        "primary",
        {"checklist": {"variants": [{"sections": [{"reference": "work.md"}]}]}},
        {"work.md": "[ ] Same gate\n"},
    )
    write_skill(root, "policy", overlay(), {"review.md": "[ ] Same gate\n  Retain evidence\n"})
    executor, step, state, directory = executor_fixture(tmp_path, monkeypatch)
    content = generate(executor, step, state, directory)
    path = directory / "checklist.md"
    # Only the overlay gate is complete, not the identical primary gate.
    path.write_text(content.replace("[ ] Same gate\n  Retain", "[x] Same gate\n  Retain"))
    if mutation == "source":
        write_skill(
            root, "replacement", overlay(), {"review.md": "[ ] Same gate\n  Retain evidence\n"}
        )
        executor.playbook["skills"]["workflow"]["shared"] = ["replacement"]
    elif mutation in {"content", "continuation"}:
        (root / "policy/references/review.md").write_text(
            "[ ] Same gate\n  Changed rule\n"
            if mutation == "continuation"
            else "[ ] Changed gate\n  Retain evidence\n"
        )
    elif mutation == "condition":
        write_skill(
            root,
            "policy",
            overlay(when={"iteration": 1}),
            {"review.md": "[ ] Same gate\n  Retain evidence\n"},
        )
    elif mutation == "missing_metadata":
        (directory / "iteration.json").unlink()
    elif mutation == "corrupt_metadata":
        (directory / "iteration.json").write_text('{"effective_checklist": {"version": 999}}')
    resumed = generate(executor, step, state, directory, preserve_completed_items=True)
    assert "[x] Same gate\n\n## Checklist source: policy" not in resumed
    assert resumed.count("[x]") == (1 if mutation == "unchanged" else 0)
    assert not validate_checklist(path).is_complete


@pytest.mark.parametrize("mutation", ["delete", "duplicate", "alter", "reorder", "continuation"])
def test_success_validation_rejects_missing_or_changed_expected_gates(
    tmp_path, monkeypatch, mutation
):
    """U09/I04: checking a partial or edited gate set is never complete."""
    from cafe.utils.checklist_validator import validate_checklist

    root = tmp_path / ".cafe/skills"
    write_skill(
        root,
        "primary",
        {"checklist": {"variants": [{"sections": [{"reference": "work.md"}]}]}},
        {"work.md": "[ ] Primary\n"},
    )
    write_skill(
        root, "policy", overlay(), {"review.md": "[ ] Policy A\n  Required rule\n[ ] Policy B\n"}
    )
    executor, step, state, directory = executor_fixture(tmp_path, monkeypatch)
    content = generate(executor, step, state, directory).replace("[ ]", "[x]")
    path = directory / "checklist.md"
    path.write_text(content)
    assert validate_checklist(path).is_complete
    if mutation == "delete":
        content = content.replace("[x] Policy B\n", "")
    elif mutation == "duplicate":
        content += "[x] Policy B\n"
    elif mutation == "alter":
        content = content.replace("Policy B", "Other task")
    elif mutation == "reorder":
        content = (
            content.replace("Policy A", "swap")
            .replace("Policy B", "Policy A")
            .replace("swap", "Policy B")
        )
    else:
        content = content.replace("Required rule", "Weakened rule")
    path.write_text(content)
    assert not validate_checklist(path).is_complete


def git_evidence(tmp_path):
    import subprocess

    def git(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args], text=True).strip()

    git("init", "-q")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    (tmp_path / ".gitignore").write_text(".cafe/\n.codex/\nglobal/\nhome/\n")
    (tmp_path / "work.txt").write_text("implemented\n")
    git("add", ".gitignore", "work.txt")
    git("commit", "-qm", "Implement work")
    return git("rev-parse", "HEAD")


def complete_ledger(directory, commit, *, only_first=False):
    import re

    path = directory / "checklist.md"
    rows = re.findall(
        r"^\[[ xX]\] `([^`]+)` — .*\(source fingerprint: ([a-f0-9]{64})\)$", path.read_text(), re.M
    )
    chosen = rows[:1] if only_first else rows
    ledger = "## Todo Progress\n"
    for handle, fingerprint in chosen:
        ledger += (
            f"\n### {handle}\n- Status: completed\n"
            f"- Source fingerprint: `{fingerprint}`\n- Files: `work.txt`\n"
            f"- Commit: `{commit}`\n- Remaining work: None.\n- Next action: Review.\n"
        )
    (directory / "output.md").write_text(ledger)
    path.write_text(path.read_text().replace("[ ]", "[x]"))
    return rows


@pytest.mark.parametrize("fallback", [False, True])
@pytest.mark.parametrize(
    "mutation", ["none", "content", "path", "version", "missing", "file", "commit"]
)
def test_all_contributors_require_distinct_live_todo_evidence(
    tmp_path, monkeypatch, fallback, mutation
):
    """I05/U11/U12: repeated producer IDs retain separate consumer evidence."""
    from cafe.core.blackboard import BlackboardStore

    root = tmp_path / ".cafe/skills"
    projection = {"todo_projection": {"artifact": "blueprint", "source": "bespoke"}}
    write_skill(
        root,
        "primary",
        {} if fallback else {"checklist": {"variants": [{"sections": [projection]}]}},
    )
    write_skill(
        root,
        "policy",
        {"checklist_overlay": {"variants": [{"sections": [projection, projection]}]}},
    )
    executor, step, state, directory = executor_fixture(tmp_path, monkeypatch)
    todo = executor.issue_dir / "blueprint.md"
    todo.write_text(
        "## Todo List\n- [ ] `TASK-001` — Source: `bespoke` — Work: implement "
        "— Closure: correct — Evidence: tests\n"
    )
    store = BlackboardStore(executor.issue_dir)
    store.set_artifact(state, "blueprint", str(todo))
    commit = git_evidence(tmp_path)
    generate(executor, step, state, directory)
    rows = complete_ledger(directory, commit, only_first=True)
    assert len(rows) == (2 if fallback else 3)
    assert len({handle for handle, _ in rows}) == len(rows)
    assert len({fingerprint for _, fingerprint in rows}) == 1
    passed, detail = executor._validate_projected_todo_completion_detail(directory / "checklist.md")
    assert not passed
    assert all(handle in detail for handle, _ in rows)
    complete_ledger(directory, commit)
    assert executor._validate_projected_todo_completion(directory / "checklist.md")
    if mutation == "content":
        todo.write_text(todo.read_text().replace("implement", "changed work"))
    elif mutation == "path":
        new = todo.with_name("replacement.md")
        new.write_text(todo.read_text())
        state.artifacts["blueprint"].path = str(new)
        store.save(state)
    elif mutation == "version":
        state.artifacts["blueprint"].version += 1
        store.save(state)
    elif mutation == "missing":
        todo.unlink()
    elif mutation == "file":
        (tmp_path / "work.txt").write_text("uncommitted\n")
    elif mutation == "commit":
        output = directory / "output.md"
        output.write_text(output.read_text().replace(commit, "a" * 40))
    assert executor._validate_projected_todo_completion(directory / "checklist.md") == (
        mutation == "none"
    )
    if mutation in {"content", "path", "version", "file", "commit"}:
        resumed = generate(executor, step, state, directory, preserve_completed_items=True)
        assert "[x]" not in resumed


class JourneyAgent:
    """Only the external agent boundary is substituted in lifecycle journeys."""

    def __init__(self, executor, actions):
        from types import SimpleNamespace

        from cafe.core.types import AgentCLI

        self.executor = executor
        self.actions = actions
        self.calls = 0
        self.prompts = []
        self.agent = SimpleNamespace(
            config=SimpleNamespace(cli=AgentCLI.CODEX, session_id=None, model=None)
        )

    def get_agent(self, name):
        return self.agent

    def execute(self, name, prompt, **kwargs):
        from cafe.core.types import TokenUsage

        self.prompts.append(prompt)
        action = self.actions[min(self.calls, len(self.actions) - 1)]
        self.calls += 1
        response = action(self.executor._get_iteration_dir(self.executor.iteration))
        return response, TokenUsage(), [], [], [], None


def lifecycle_fixture(tmp_path, monkeypatch, *, real_develop=False):
    root = tmp_path / ".cafe/skills"
    if real_develop:
        write_skill(root, "primary")
    if not real_develop:
        write_skill(
            root,
            "primary",
            {"checklist": {"variants": [{"sections": [{"reference": "work.md"}]}]}},
            {"work.md": "[ ] Primary\n"},
        )
    write_skill(root, "policy", overlay(), {"review.md": "[ ] Independent policy\n"})
    executor, step, state, directory = executor_fixture(
        tmp_path, monkeypatch, primary="cafe-develop" if real_develop else "primary"
    )
    step.update(
        {
            "output_artifact": "result",
            "behavior": {"completion": "status_code"},
            "on": {
                "await_agent": "inspect",
                "no_changes_needed": "inspect",
                "need_clarification": "assemble",
                "need_permission": "assemble",
                "manual_handoff": "inspect",
            },
        }
    )
    executor.playbook["steps"]["inspect"] = {
        "skill": "primary",
        "role": "developer",
        "on": {"await_agent": "_done"},
    }
    git_evidence(tmp_path)
    (tmp_path / ".cafe/phases.yaml").write_text(
        "assemble:\n  name: David\n  clis: [{cli: codex, model: test}]\n"
    )
    return executor, step, state, directory


@pytest.mark.parametrize("route", ["baton", "legacy", "automatic_no_change"])
@pytest.mark.parametrize("repair", [False, True])
def test_success_routes_require_overlay_before_transition(tmp_path, monkeypatch, route, repair):
    """I06/U13: actual executor transitions require all effective gates."""
    import json

    from cafe.core.blackboard import BlackboardStore

    executor, step, state, directory = lifecycle_fixture(tmp_path, monkeypatch)

    def finish(directory):
        path = directory / "checklist.md"
        content = path.read_text().replace("[ ] Primary", "[x] Primary")
        if repair and executor.agent_manager.calls > 1:
            content = content.replace("[ ]", "[x]")
        path.write_text(content)
        (directory / "output.md").write_text("# Result\n")
        if route == "baton":
            (executor.issue_dir / "next_step.txt").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "to_owner": "agent",
                        "to_step": "inspect",
                        "intent": "await_agent",
                    }
                )
            )
            return ""
        return "no_changes_needed" if route == "automatic_no_change" else "confirmed"

    executor.agent_manager = JourneyAgent(executor, [finish])
    result = executor.execute_step("assemble", step, state)
    assert result.artifact_ready == repair
    assert executor.agent_manager.calls == (2 if repair else 4)
    handoff = BlackboardStore(executor.issue_dir).load_or_create("assemble").handoff_contract
    if repair:
        assert handoff.to_step == "inspect"
    else:
        assert handoff is None or handoff.to_step != "inspect"


@pytest.mark.parametrize("intent", ["need_clarification", "need_permission", "manual_handoff"])
def test_help_routes_do_not_require_overlay_completion(tmp_path, monkeypatch, intent):
    """I07/U13: incomplete gates leave existing help/manual routes usable."""
    import json

    executor, step, state, directory = lifecycle_fixture(tmp_path, monkeypatch)

    def help_request(directory):
        (directory / "output.md").write_text("Help needed\n")
        (executor.issue_dir / "next_step.txt").write_text(
            json.dumps(
                {
                    "version": 1,
                    "to_owner": "agent" if intent == "manual_handoff" else "user",
                    "to_step": "inspect" if intent == "manual_handoff" else "user",
                    "intent": intent,
                }
            )
        )
        return ""

    executor.agent_manager = JourneyAgent(executor, [help_request])
    result = executor.execute_step("assemble", step, state)
    assert executor.agent_manager.calls == 1
    assert not any(event["type"] == "checklist_validation_failed" for event in result.events)


@pytest.mark.parametrize("metadata_state", ["intact", "missing", "corrupt"])
@pytest.mark.parametrize("decision_target", ["inspect", "assemble"])
def test_real_no_change_human_task_returns_to_finish_gates_without_reasking(
    tmp_path, monkeypatch, metadata_state, decision_target
):
    """I08: durable agreement cannot send unfinished Develop overlays to its target."""
    from cafe.core.blackboard import BlackboardStore
    from cafe.core.human_task_records import HumanTaskRecordStore, HumanTaskStatus
    from cafe.ui.human_tasks import apply_human_task_payload

    executor, step, state, directory = lifecycle_fixture(tmp_path, monkeypatch, real_develop=True)
    step["human_tasks"] = [
        {
            "trigger": "no_changes_needed",
            "task_id": "no-change-decision",
            "outcomes": {"agree": decision_target, "disagree": "assemble"},
        }
    ]
    step["hooks"] = {
        "prepare_input": ["UserInputCollector"],
        "after_execute": ["NoChangesNeededHandler"],
    }
    store = BlackboardStore(executor.issue_dir)
    plan = executor.issue_dir / "blueprint.md"
    plan.write_text("## Todo List\nNo actionable work.\n")
    store.set_artifact(state, "plan", str(plan))

    def no_change(directory):
        (directory / "output.md").write_text("No implementation changes are necessary.\n")
        return "no_changes_needed"

    executor.agent_manager = JourneyAgent(executor, [no_change])
    from cafe.core.workflow_runtime import BlackboardWorkflowRuntime

    first = BlackboardWorkflowRuntime(
        issue_dir=executor.issue_dir, playbook=executor.playbook, executor=executor.execute_step
    ).run(start_step="assemble")
    assert not first.completed
    assert executor.agent_manager.calls == 1
    state = store.load_or_create("assemble")
    records = HumanTaskRecordStore(executor.issue_dir)
    task = records.tasks()[0]
    if metadata_state == "missing":
        (directory / "iteration.json").unlink()
    elif metadata_state == "corrupt":
        (directory / "iteration.json").write_text('{"effective_checklist": {"version": 999}}')
    applied = apply_human_task_payload(
        issue_dir=executor.issue_dir,
        playbook_data=executor.playbook,
        blackboard=state,
        from_step="assemble",
        trigger="no_changes_needed",
        raw_payload={"task": task.policy_id, "human_task_id": task.id, "decision": "agree"},
        source="test",
    )
    assert applied.target == "assemble"
    assert records.get_task(task.id).status is HumanTaskStatus.COMPLETED

    def finish(directory):
        path = directory / "checklist.md"
        path.write_text(path.read_text().replace("[ ]", "[x]"))
        (directory / "output.md").write_text("No changes. All applicable review gates completed.\n")
        return "confirmed"

    executor.agent_manager = JourneyAgent(executor, [finish])
    result = executor.execute_step("assemble", step, store.load_or_create("assemble"))
    assert result.artifact_ready
    assert executor.agent_manager.calls == 1
    assert store.load_or_create("assemble").handoff_contract.to_step == "inspect"
    assert len(records.tasks()) == 1


def test_exact_duplicate_blocks_keep_independent_completion_on_resume(tmp_path, monkeypatch):
    """U09/U10: identical complete blocks still have distinct source identities."""
    root = tmp_path / ".cafe/skills"
    write_skill(
        root,
        "primary",
        {"checklist": {"variants": [{"sections": [{"reference": "work.md"}]}]}},
        {"work.md": "[ ] Same gate\n"},
    )
    write_skill(root, "policy", overlay(), {"review.md": "[ ] Same gate\n"})
    executor, step, state, directory = executor_fixture(tmp_path, monkeypatch)
    content = generate(executor, step, state, directory)
    first, last = content.rsplit("[ ] Same gate", 1)
    (directory / "checklist.md").write_text(first + "[x] Same gate" + last)
    resumed = generate(executor, step, state, directory, preserve_completed_items=True)
    assert resumed.split("## Checklist source: policy")[0].count("[x]") == 0
    assert resumed.count("[x]") == 1


def test_deleted_checklist_is_rebuilt_from_all_contributors_before_retry(tmp_path, monkeypatch):
    """I06/U13: recovery cannot replace effective gates with a generic placeholder."""
    executor, step, state, directory = lifecycle_fixture(tmp_path, monkeypatch)

    def finish(directory):
        path = directory / "checklist.md"
        (directory / "output.md").write_text("# Result\n")
        if executor.agent_manager.calls == 1:
            path.unlink()
        else:
            content = path.read_text()
            assert "Independent policy" in content and "Primary" in content
            path.write_text(content.replace("[ ]", "[x]"))
        return "confirmed"

    executor.agent_manager = JourneyAgent(executor, [finish])
    result = executor.execute_step("assemble", step, state)
    assert result.artifact_ready
    assert executor.agent_manager.calls == 2


def test_iteration_primary_and_replaced_injections_keep_resolved_order(tmp_path, monkeypatch):
    """I02/I03/U06: production resolution does not resurrect replaced contributions."""
    root = tmp_path / ".cafe/skills"
    for name in ("primary", "later"):
        write_skill(
            root,
            name,
            {"checklist": {"variants": [{"sections": [{"reference": "work.md"}]}]}},
            {"work.md": f"[ ] {name}\n"},
        )
    for name in ("removed", "role_policy", "step_policy"):
        write_skill(root, name, overlay(), {"review.md": f"[ ] {name}\n"})
    executor, step, state, directory = executor_fixture(tmp_path, monkeypatch, iteration=2)
    step["skill"] = {"1": "primary", "default": "later"}
    executor.playbook["skills"]["workflow"] = {
        "shared": ["removed"],
        "roles": {"developer": {"mode": "replace", "skills": ["role_policy"]}},
        "steps": {
            "assemble": {"mode": "extend", "skills": ["later", "role_policy", "step_policy"]}
        },
    }
    content = generate(executor, step, state, directory)
    rows = [line for line in content.splitlines() if line.startswith("[ ]")]
    assert rows == ["[ ] later", "[ ] role_policy", "[ ] step_policy"]


def test_completed_decision_hook_cannot_skip_effective_gate_validation(tmp_path, monkeypatch):
    """U13/I08: even hook-only success must invoke the agent if gates remain."""
    from cafe.core.blackboard import BlackboardStore

    executor, step, state, directory = lifecycle_fixture(tmp_path, monkeypatch)
    # Use a real reusable decision policy and UserInputCollector hook. This
    # exercises the older hook-only return path as well as the durable journey.
    policy = {
        "id": "decision",
        "pattern": "no_changes_needed",
        "prompt": "Accept?",
        "input_schema": "decision",
        "decisions": [{"id": "agree", "label": "Agree"}],
    }
    primary = {
        "human_tasks": [policy],
        "checklist": {"variants": [{"sections": [{"reference": "work.md"}]}]},
    }
    write_skill(tmp_path / ".cafe/skills", "primary", primary, {"work.md": "[ ] Primary\n"})
    step["human_tasks"] = [
        {"trigger": "no_changes_needed", "task_id": "decision", "outcomes": {"agree": "inspect"}}
    ]
    step["hooks"] = {"prepare_input": ["UserInputCollector"]}
    store = BlackboardStore(executor.issue_dir)
    store.record_event(
        state, "step_completed", {"step": "assemble", "status_code": "no_changes_needed"}
    )
    (directory / "iteration.json").write_text(
        '{"iteration": 1, "status_code": "no_changes_needed", "end_time": "2026-01-01"}'
    )
    executor.user_input = '{"task": "decision", "decision": "agree"}'

    def finish(directory):
        path = directory / "checklist.md"
        assert "Independent policy" in path.read_text()
        path.write_text(path.read_text().replace("[ ]", "[x]"))
        (directory / "output.md").write_text("# Result\n")
        return "confirmed"

    executor.agent_manager = JourneyAgent(executor, [finish])
    result = executor.execute_step("assemble", step, state)
    assert result.artifact_ready
    assert executor.agent_manager.calls == 1


def test_manual_handoff_still_validates_todos_required_by_target_overlay(tmp_path, monkeypatch):
    """I07/U11/U13: help exemptions do not remove independent output contracts."""
    import json

    executor, step, state, directory = lifecycle_fixture(tmp_path, monkeypatch)
    root = tmp_path / ".cafe/skills"
    write_skill(root, "receiver")
    write_skill(
        root,
        "todo_policy",
        {
            "checklist_overlay": {
                "variants": [
                    {"sections": [{"todo_projection": {"artifact": "active_work", "causal": True}}]}
                ]
            }
        },
    )
    target = executor.playbook["steps"]["inspect"]
    target.update({"skill": "receiver", "input_artifacts": ["result"]})
    executor.playbook["skills"]["workflow"]["steps"] = {
        "inspect": {"mode": "extend", "skills": ["todo_policy"]}
    }

    def handoff(directory):
        (directory / "output.md").write_text(
            "## Todo List\nNo actionable work.\n"
            if executor.agent_manager.calls > 1
            else "# Missing required Todo List\n"
        )
        (executor.issue_dir / "next_step.txt").write_text(
            json.dumps(
                {
                    "version": 1,
                    "to_owner": "agent",
                    "to_step": "inspect",
                    "intent": "manual_handoff",
                }
            )
        )
        return ""

    executor.agent_manager = JourneyAgent(executor, [handoff])
    result = executor.execute_step("assemble", step, state)
    assert result.artifact_ready
    assert executor.agent_manager.calls == 2


@pytest.mark.parametrize(
    "counts", [(0, 0), (0, 1), (10, 10), (99, 1), (100, 1), (50, 50), (51, 51)]
)
def test_executor_bounds_combined_todo_work_before_agent_retry(tmp_path, monkeypatch, counts):
    """U11/U12, I04/I05/I06: accepted aggregate work can finish and resume."""
    import subprocess

    from cafe.core.blackboard import BlackboardStore
    from cafe.core.todo import MAX_TODO_ITEMS
    from cafe.utils.checklist_validator import validate_checklist

    executor, step, state, directory = lifecycle_fixture(tmp_path, monkeypatch)
    store = BlackboardStore(executor.issue_dir)
    # Keep an existing generation to verify rejection cannot partially replace it.
    generate(executor, step, state, directory)
    paths = [directory / "checklist.md", directory / "iteration.json"]
    from cafe.core.checklist import load_materialization

    before = (paths[0].read_bytes(), load_materialization(paths[1]))
    for name, field, artifact, count in [
        ("primary", "checklist", "first", counts[0]),
        ("policy", "checklist_overlay", "second", counts[1]),
    ]:
        # Equal counts deliberately project the same producer through two contributors.
        if counts[0] == counts[1]:
            artifact = "shared_work"
        write_skill(
            tmp_path / ".cafe/skills",
            name,
            {
                field: {
                    "variants": [
                        {
                            "sections": [
                                {"todo_projection": {"artifact": artifact, "source": "bespoke"}}
                            ]
                        }
                    ]
                }
            },
        )
        source = executor.issue_dir / f"{artifact}.md"
        source.write_text(
            "## Todo List\n"
            + (
                "".join(
                    f"- [ ] `TASK-{i:03}` — Source: `bespoke` — Work: task {i} "
                    "— Closure: correct — Evidence: tests\n"
                    for i in range(1, count + 1)
                )
                if count
                else "No actionable work.\n"
            )
        )
        store.set_artifact(state, artifact, str(source))
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()

    def finish(directory):
        complete_ledger(directory, commit)
        return "confirmed"

    executor.agent_manager = JourneyAgent(executor, [finish])
    if sum(counts) > MAX_TODO_ITEMS:
        with pytest.raises(ValueError) as caught:
            executor.execute_step("assemble", step, state)
        diagnostic = str(caught.value)
        assert all(
            value in diagnostic for value in ("assemble", "policy", "checklist_overlay", "100")
        )
        assert executor.agent_manager.calls == 0
        assert (paths[0].read_bytes(), load_materialization(paths[1])) == before
        return

    result = executor.execute_step("assemble", step, state)
    assert result.artifact_ready
    assert executor.agent_manager.calls == 1
    assert store.load_or_create("assemble").handoff_contract.to_step == "inspect"
    generate(executor, step, state, directory, preserve_completed_items=True)
    assert validate_checklist(directory / "checklist.md").is_complete
    assert executor._validate_projected_todo_completion(directory / "checklist.md")

    if counts == (99, 1):
        import json

        metadata = json.loads(paths[1].read_text())
        bindings = metadata["effective_checklist"]["projections"]
        bindings.append(bindings[-1])
        paths[1].write_text(json.dumps(metadata))
        assert not validate_checklist(paths[0]).is_complete
        restored = generate(executor, step, state, directory, preserve_completed_items=True)
        assert "[x]" not in restored
        finish(directory)
        assert validate_checklist(paths[0]).is_complete
        assert executor._validate_projected_todo_completion(paths[0])
