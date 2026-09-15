"""Subprocess workers for public artifact-contract runtime journeys."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

from cafe.core.git import GitOperations
from cafe.core.types import AgentCLI, TokenUsage
from cafe.core.workflow_models import StepExecutionResult
from cafe.core.workflow_runtime import BlackboardWorkflowRuntime
from cafe.phases.generic_phase import GenericPhase
from cafe.phases.generic_workflow_step import GenericWorkflowStepExecutor
from cafe.playbooks.loader import PlaybookLoader
from cafe.skills.loader import SkillLoader
from cafe.skills.native_bridge import NativeSkillBridge
from cafe.verification import run_verification


def _runtime_result(result: object, *, calls: int) -> None:
    print(
        json.dumps(
            {
                "completed": bool(getattr(result, "completed", False)),
                "final_step": str(getattr(result, "final_step", "")),
                "final_status_code": str(getattr(result, "final_status_code", "")),
                "calls": calls,
            }
        )
    )


class _CustomProductionAgent:
    def __init__(self, repo: Path) -> None:
        self.repo = repo
        self.agent = SimpleNamespace(
            config=SimpleNamespace(cli=AgentCLI.CODEX, session_id="custom-session", model=None)
        )
        self.calls = 0

    def get_agent(self, _name: str) -> SimpleNamespace:
        return self.agent

    def _workflow_inputs(self, skill_name: str) -> dict[str, Path]:
        skill_file = self.repo / ".codex" / "skills" / skill_name / "SKILL.md"
        lines = skill_file.read_text(encoding="utf-8").splitlines()
        inputs: dict[str, Path] = {}
        for line in lines:
            if not line.startswith("- ") or ": " not in line:
                continue
            placeholder, raw_path = line[2:].split(": ", 1)
            if placeholder.startswith("custom_"):
                path = Path(raw_path.strip())
                inputs[placeholder] = path if path.is_absolute() else self.repo / path
        return inputs

    def execute(self, _name: str, _prompt: str, *, streaming_output_file=None, **_kwargs):
        self.calls += 1
        if streaming_output_file is None:
            raise AssertionError("the production runtime must provide an output path")
        iteration_dir = Path(streaming_output_file).parent
        step_name = iteration_dir.parent.name
        output = iteration_dir / "output.md"
        if step_name == "emit":
            output.write_text(
                "## Todo List\n"
                "- [ ] `CUST-001` — Source: `custom_review` — Work: preserve custom route — "
                "Closure: consumed after restart — Evidence: production journey\n",
                encoding="utf-8",
            )
        else:
            inputs = self._workflow_inputs("custom-consumer")
            expected = {"custom_document", "custom_correction", "custom_workspace"}
            if set(inputs) != expected:
                raise AssertionError(f"declared consumer inputs were not installed: {sorted(inputs)}")
            document = inputs["custom_document"]
            correction = inputs["custom_correction"]
            workspace = inputs["custom_workspace"]
            if not document.is_file() or not correction.is_file() or not workspace.is_file():
                raise AssertionError("the consumer received an unreadable declared input")
            if "CUST-001" not in document.read_text(encoding="utf-8"):
                raise AssertionError("the consumer did not receive the document contents")
            if "CUST-001" not in correction.read_text(encoding="utf-8"):
                raise AssertionError("the consumer did not receive the correction contents")
            workspace_record = json.loads(workspace.read_text(encoding="utf-8"))
            if workspace_record.get("name") != "verified_state":
                raise AssertionError("the consumer did not receive the verified workspace")
            output.write_text("# Consumer result\n", encoding="utf-8")
        run_verification(
            output_file=output,
            command=[sys.executable, "-c", "print('custom journey')"],
            scope="targeted",
            cwd=self.repo,
        )
        checklist = iteration_dir / "checklist.md"
        checklist.write_text(
            "\n".join(
                line.replace("[ ]", "[x]", 1) if line.startswith("[ ]") else line
                for line in checklist.read_text(encoding="utf-8").splitlines()
            )
            + "\n",
            encoding="utf-8",
        )
        return "await_agent", TokenUsage(), [], [], [], None


class _LegacyProductionAgent:
    def __init__(self, repo: Path, issue_dir: Path) -> None:
        self.repo = repo
        self.issue_dir = issue_dir
        self.agent = SimpleNamespace(
            config=SimpleNamespace(cli=AgentCLI.CODEX, session_id="legacy-session", model=None)
        )
        self.calls = 0

    def get_agent(self, _name: str) -> SimpleNamespace:
        return self.agent

    def execute(self, _name: str, _prompt: str, *, streaming_output_file=None, **_kwargs):
        self.calls += 1
        if streaming_output_file is None:
            raise AssertionError("the production runtime must provide an output path")
        skill_file = self.repo / ".codex" / "skills" / "legacy-consumer" / "SKILL.md"
        inputs = {
            placeholder: Path(raw_path.strip())
            for placeholder, raw_path in (
                line[2:].split(": ", 1)
                for line in skill_file.read_text(encoding="utf-8").splitlines()
                if line.startswith("- legacy_") and ": " in line
            )
        }
        artifact = inputs.get("legacy_artifact")
        if artifact is None:
            raise AssertionError("the legacy skill did not receive its declared artifact")
        if not artifact.is_absolute():
            artifact = self.issue_dir / artifact
        if artifact.read_text(encoding="utf-8") != "legacy development summary\n":
            raise AssertionError("the legacy consumer did not receive the persisted artifact")
        iteration_dir = Path(streaming_output_file).parent
        output = iteration_dir / "output.md"
        output.write_text("# Resumed legacy consumer\n", encoding="utf-8")
        run_verification(
            output_file=output,
            command=[sys.executable, "-c", "print('legacy consumer')"],
            scope="targeted",
            cwd=self.repo,
        )
        checklist = iteration_dir / "checklist.md"
        checklist.write_text(
            "\n".join(
                line.replace("[ ]", "[x]", 1) if line.startswith("[ ]") else line
                for line in checklist.read_text(encoding="utf-8").splitlines()
            )
            + "\n",
            encoding="utf-8",
        )
        return "await_agent", TokenUsage(), [], [], [], None


def _generic_runtime_executor(
    *, repo: Path, issue_dir: Path, playbook: dict, builtin_root: Path, global_root: Path,
    agent_manager: object, role_agent_map: dict[str, str],
):
    loader = SkillLoader(
        project_root=repo,
        global_root=global_root,
        builtin_root=builtin_root,
    )
    phase = GenericPhase(
        loader,
        skill_bridge=NativeSkillBridge(loader, project_root=repo, home_dir=global_root / "home"),
    )
    executor = GenericWorkflowStepExecutor(
        issue_dir=issue_dir,
        issue_name=str(playbook["playbook"]["id"]),
        playbook=playbook,
        generic_phase=phase,
        agent_manager=agent_manager,
        git_ops=GitOperations(repo),
        role_agent_map=role_agent_map,
    )

    def execute(step_name: str, step_def: dict, state: object, **kwargs) -> StepExecutionResult:
        result = executor.execute_step(step_name, step_def, state, **kwargs)
        # The bounded v0.2 skill has no current checklist builder. Complete
        # its generated placeholder through the public executor adapter before
        # the runtime validates the persisted step result.
        checklist = issue_dir / step_name / f"iteration_{executor.iteration:03d}" / "checklist.md"
        if checklist.is_file():
            checklist.write_text(
                checklist.read_text(encoding="utf-8").replace("[ ]", "[x]"),
                encoding="utf-8",
            )
        return result

    return execute, executor


def run_custom_runtime_process(
    repo_value: str, issue_value: str, builtin_value: str, global_value: str, mode: str
) -> None:
    repo = Path(repo_value)
    issue_dir = Path(issue_value)
    builtin_root = Path(builtin_value)
    global_root = Path(global_value)
    os.chdir(repo)
    playbook = PlaybookLoader(
        project_root=repo, global_root=global_root, builtin_root=builtin_root
    ).load("custom-contract", strict=True)
    manager = _CustomProductionAgent(repo)
    execute, _executor = _generic_runtime_executor(
        repo=repo,
        issue_dir=issue_dir,
        playbook=playbook,
        builtin_root=builtin_root,
        global_root=global_root,
        agent_manager=manager,
        role_agent_map={"producer": "custom-agent"},
    )
    runtime = BlackboardWorkflowRuntime(issue_dir=issue_dir, playbook=playbook, executor=execute)
    if mode == "emit":
        result = runtime.run(start_step="emit", single_step=True)
    elif mode == "consume":
        result = runtime.run()
    else:
        raise ValueError(f"unknown custom runtime mode: {mode}")
    _runtime_result(result, calls=manager.calls)


def run_legacy_runtime_process(
    repo_value: str, issue_value: str, builtin_value: str, global_value: str
) -> None:
    repo = Path(repo_value)
    issue_dir = Path(issue_value)
    builtin_root = Path(builtin_value)
    global_root = Path(global_value)
    os.chdir(repo)
    playbook = PlaybookLoader(
        project_root=repo, global_root=global_root, builtin_root=builtin_root
    ).load("legacy-contract")
    manager = _LegacyProductionAgent(repo, issue_dir)
    execute, _executor = _generic_runtime_executor(
        repo=repo,
        issue_dir=issue_dir,
        playbook=playbook,
        builtin_root=builtin_root,
        global_root=global_root,
        agent_manager=manager,
        role_agent_map={"operator": "legacy-consumer-agent"},
    )
    result = BlackboardWorkflowRuntime(
        issue_dir=issue_dir, playbook=playbook, executor=execute
    ).run()
    _runtime_result(result, calls=manager.calls)
