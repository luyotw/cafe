"""Direct playbook step execution through GenericPhase."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from cafe.agents.manager import AgentManager
from cafe.core.blackboard import (
    ArtifactEntry,
    ArtifactKind,
    BlackboardState,
    BlackboardStore,
    HandoffContract,
    HandoffIntent,
    HandoffOwner,
)
from cafe.core.capabilities import CAPABILITY_PR_PUBLISH_ID
from cafe.core.git import GitOperations
from cafe.core.phase import Phase
from cafe.core.resume_user_input import (
    is_resume_iteration,
    load_prior_run_context,
    prior_cli_and_session,
    resolve_resume_user_input,
)
from cafe.core.status_codes import (
    PhaseStatusCode,
    StatusCodeParser,
    step_on_declares,
    transition_map_key,
)
from cafe.core.workflow_models import StepExecutionResult
from cafe.phases.generic_phase import GenericPhase
from cafe.skills.checklist_composer import compose_declared_checklist
from cafe.skills.contracts import (
    DeclaredArtifactError,
    SkillWorkflowContract,
    resolve_prompt_inputs,
)
from cafe.skills.loader import SkillLoader, canonical_skill_name
from cafe.templates.manager import TemplateManager
from cafe.utils.git_utils import get_git_toplevel, get_repo_root, to_cwd_relative_path
from cafe.utils.phase_config import load_phase_step_model


def align_pr_baton_after_execution(
    *,
    issue_dir: Path,
    playbook: Dict[str, Any],
    blackboard_state: BlackboardState,
    step_name: str,
    status_code: Optional[str],
) -> None:
    """If PR feedback needs code changes, ensure the baton leaves the PR step.

    Agents should update ``next_step.txt`` themselves, but when they return
    ``manual_handoff`` while the baton still targets ``pr``, advance it to
    ``develop`` so ``BlackboardWorkflowRuntime`` can transition.
    """
    if step_name != "pr" or not status_code:
        return
    if status_code != PhaseStatusCode.NEEDS_CHANGES.value:
        return

    allowed = list(playbook.get("steps", {}).keys())
    store = BlackboardStore(issue_dir)
    contract = store.load_handoff_contract(
        blackboard_state,
        allowed_steps=allowed,
    )
    if contract.to_owner != HandoffOwner.AGENT or contract.to_step != "pr":
        return

    store.update_handoff_contract(
        blackboard_state,
        from_step="pr",
        to_owner=HandoffOwner.AGENT,
        to_step="develop",
        intent=HandoffIntent.AWAIT_AGENT,
        status_code=status_code,
        source="workflow.pr_needs_changes",
    )
    store.set_handoff_summary(
        blackboard_state,
        "PR feedback requires development work; next playbook step is develop.",
    )


class GenericWorkflowStepExecutor(Phase):
    """Execute one playbook step without shelling out to legacy CLI commands."""

    SHARED_WORKFLOW_SKILLS = ["cafe-workflow-common", "cafe-github_sync"]
    BLACKBOARD_DIGEST_EVENT_LIMIT = 5
    BLACKBOARD_DIGEST_ARTIFACT_LIMIT = 20
    BLACKBOARD_DIGEST_TEXT_LIMIT = 240

    def _get_skill_loader(self) -> SkillLoader:
        """Return the GenericPhase loader, with a standalone-test fallback."""
        generic_phase = getattr(self, "generic_phase", None)
        return getattr(generic_phase, "skill_loader", None) or SkillLoader()

    def __init__(
        self,
        *,
        issue_dir: Path,
        issue_name: str,
        playbook: Dict[str, Any],
        generic_phase: GenericPhase,
        agent_manager: AgentManager,
        git_ops: GitOperations,
        role_agent_map: Dict[str, str],
        role_configs: Optional[Dict[str, Dict[str, Any]]] = None,
        step_user_inputs: Optional[Dict[str, str]] = None,
        interactive: bool = False,
        config_allowed_directories: Optional[List[str]] = None,
        extra_allowed_directories: Optional[List[str]] = None,
    ) -> None:
        self.interactive = interactive
        self.issue_dir = issue_dir
        self.issue_name = issue_name
        self.playbook = playbook
        self.generic_phase = generic_phase
        self.agent_manager = agent_manager
        self.git_ops = git_ops
        self.role_agent_map = role_agent_map
        self.role_configs = dict(role_configs or {})
        self.step_user_inputs = dict(step_user_inputs or {})
        self.phase_name = ""
        self.phase_dir = issue_dir
        self.iteration = 0
        self._current_output_file: Optional[Path] = None
        self._resolved_iteration_user_input: Optional[str] = None
        self._config_allowed_directories: List[str] = list(config_allowed_directories or [])
        self._extra_allowed_directories: List[str] = list(extra_allowed_directories or [])
        self._template_allowed_directories: List[str] = []

    def _get_allowed_directories(self) -> List[str]:
        base = super()._get_allowed_directories()
        merged = list(
            dict.fromkeys(
                base
                + self._config_allowed_directories
                + self._extra_allowed_directories
                + self._template_allowed_directories
            )
        )
        return merged

    def execute(self) -> Any:
        raise NotImplementedError("GenericWorkflowStepExecutor only supports execute_step()")

    def execute_step(
        self,
        step_name: str,
        step_def: Dict[str, Any],
        blackboard_state: BlackboardState,
        extra_prompt: Optional[str] = None,
    ) -> StepExecutionResult:
        self.phase_name = step_name
        self.phase_dir = self.issue_dir / step_name
        self.phase_dir.mkdir(parents=True, exist_ok=True)

        self.iteration = self._get_next_iteration_number(step_name, self.phase_dir)
        self._resolved_iteration_user_input = None
        iteration_dir = self._get_iteration_dir(self.iteration)
        iteration_dir.mkdir(parents=True, exist_ok=True)

        output_file = self._get_versioned_file_path(step_name, self.iteration, self.phase_dir)
        checklist_file = iteration_dir / "checklist.md"
        questions_xml_file = iteration_dir / "questions.xml"
        capability_request_file = iteration_dir / "capability_request.json"
        publish_request_file = iteration_dir / "publish_request.json"
        self._current_output_file = output_file

        if self.iteration > 1:
            self._copy_previous_version(step_name, self.iteration, self.phase_dir)
        self._ensure_output_file_initialized(step_name, output_file)
        capability_ids = self._effective_capability_ids(step_name, step_def)
        if capability_ids:
            self._write_capability_request(
                output_file=output_file,
                capability_request_file=capability_request_file,
                capability_ids=capability_ids,
            )
            if step_name == "pr":
                self._write_publish_request(
                    output_file=output_file,
                    publish_request_file=publish_request_file,
                )

        skill_name = self._resolve_skill_name(step_def, self.iteration)
        valid_intents = self._resolve_valid_intents(step_def)
        agent_name = self._resolve_agent_name(step_name, step_def)
        self._step_agent_name = agent_name
        self._apply_step_agent_model(step_name=step_name, step_def=step_def, agent_name=agent_name)
        agent_cli = self.agent_manager.get_agent(agent_name).config.cli
        context = self._build_context(
            step_name=step_name,
            step_def=step_def,
            blackboard_state=blackboard_state,
            agent_name=agent_name,
            output_file=output_file,
        )
        contract = self._get_skill_loader().get_workflow_contract(skill_name)
        self._template_allowed_directories = self._template_allowed_directories_for(
            step_name=step_name,
            step_def=step_def,
            skill_name=skill_name,
            contract=contract,
        )
        shared_skill_invocations = self.generic_phase.prepare_skills(
            skill_names=self.SHARED_WORKFLOW_SKILLS,
            agent_cli=agent_cli,
            context=context,
        )
        skill_invocation = self.generic_phase.prepare_skill(
            skill_name=skill_name,
            agent_cli=agent_cli,
            context=context,
        )
        self._generate_checklist(
            step_name=step_name,
            skill_name=skill_name,
            agent_name=agent_name,
            step_def=step_def,
            blackboard_state=blackboard_state,
            checklist_file=checklist_file,
            output_file=output_file,
            questions_xml_file=questions_xml_file,
        )

        last_prompt: List[str] = []
        allowed_tools = self._build_allowed_tools(
            step_name=step_name,
            step_def=step_def,
            output_file=output_file,
            checklist_file=checklist_file,
            questions_xml_file=questions_xml_file,
        )
        phase_specific_data = {
            "step_name": step_name,
            "skill_name": skill_name,
            "playbook_id": self.playbook.get("playbook", {}).get("id"),
        }
        require_status_code = self._step_requires_status_code(step_name)

        def run_agent(prompt: str) -> str:
            last_prompt[:] = [prompt]
            resolved_user_input = self._get_resolved_iteration_user_input(step_name)
            if extra_prompt:
                resolved_user_input = (
                    f"{extra_prompt}\n\n{resolved_user_input}"
                    if resolved_user_input
                    else extra_prompt
                )
            attempt_allowed_tools = (
                self._build_baton_retry_allowed_tools()
                if self._is_baton_retry_user_input(resolved_user_input)
                else allowed_tools
            )
            response, _ = self._execute_agent_iteration(
                agent_name=agent_name,
                prompt=prompt,
                user_input=resolved_user_input,
                valid_intents=valid_intents,
                require_status_code=False,
                persist_status=False,
                allowed_tools=attempt_allowed_tools,
                phase_specific_data=phase_specific_data,
            )
            return response

        execution = self.generic_phase.execute(
            skill_name=skill_name,
            step_def=step_def,
            agent_executor=run_agent,
            skill_invocation=skill_invocation,
            shared_skill_invocations=shared_skill_invocations,
            context=context,
            output_file=output_file,
            checklist_file=checklist_file,
            questions_xml_file=questions_xml_file,
            hook_context={
                "phase": self,
                "step_name": step_name,
                "agent_name": agent_name,
                "iteration_dir": iteration_dir,
                "output_file": output_file,
                "questions_xml_file": questions_xml_file,
                "capability_request_file": capability_request_file if capability_ids else None,
                "publish_request_file": publish_request_file if step_name == "pr" else None,
                "blackboard_state": blackboard_state,
                "transform_runtime_context": (
                    lambda runtime_context: self._apply_resume_to_runtime_context(
                        runtime_context,
                        step_name,
                    )
                ),
            },
        )

        response = execution.response
        status_code = execution.status_code
        if require_status_code:
            if status_code is None:
                status_code = StatusCodeParser.extract(response, valid_intents)

        agent_was_invoked = bool(last_prompt)
        if (
            require_status_code
            and agent_was_invoked
            and execution.status_code is not None
            and status_code is not None
            and self._should_validate_checklist(status_code)
        ):
            resolved_user_input = self._get_resolved_iteration_user_input(step_name)
            response, validated_status, validation_passed = (
                self._validate_and_retry_checklist_completion(
                    agent_name=agent_name,
                    prompt=last_prompt[0] if last_prompt else "",
                    user_input=resolved_user_input,
                    valid_intents=valid_intents,
                    allowed_tools=allowed_tools,
                    max_retries=3,
                )
            )
            if validation_passed and validated_status is not None:
                status_code = validated_status

        output_key = str(step_def.get("output_artifact", step_name))
        artifacts: Dict[str, str] = {}
        if execution.artifact_ready and output_file.exists():
            output_path = str(output_file)
            artifacts[output_key] = output_path
            self._write_artifact_record(
                blackboard_state=blackboard_state,
                output_key=output_key,
                output_path=output_path,
                updated_by=step_name,
            )

        auto_continue = any(
            self._event_allows_auto_continue(event)
            for event in execution.events
            if isinstance(event, dict)
        )
        # READY_FOR_REVIEW / CONFIRM_OUTPUT / NEED_CLARIFICATION always
        # hand off to the user step.  In interactive mode the user sees
        # output and confirm/modify options via _handle_user_phase.  In
        # non-interactive mode the workflow stops and the caller provides
        # input via --user-input.
        # auto_continue stays False → handoff to user.

        effective_status = status_code

        events = [event for event in execution.events if isinstance(event, dict)]
        store = BlackboardStore(self.issue_dir)
        for event in events:
            if event.get("type") != "script_hook":
                continue
            payload = dict(event)
            payload.setdefault("step", step_name)
            store.record_event(blackboard_state, "script_hook", payload)

        if require_status_code and effective_status is not None:
            handoff_intent = self._resolve_handoff_intent(step_def, effective_status)
            if handoff_intent is not None:
                events.append(
                    {
                        "type": "handoff_intent",
                        "step": step_name,
                        "intent": handoff_intent,
                    }
                )

        if status_code is not None:
            # If the agent already wrote a valid baton (next_step.txt),
            # skip the status-code-driven baton write so we don't overwrite
            # the agent's explicit handoff.  This is the baton-first path.
            if not self._agent_wrote_baton(step_name):
                self._write_status_transition_handoff(
                    blackboard_state=blackboard_state,
                    step_name=step_name,
                    step_def=step_def,
                    response=response,
                    status_code=effective_status,
                    auto_continue=auto_continue,
                )
            align_pr_baton_after_execution(
                issue_dir=self.issue_dir,
                playbook=self.playbook,
                blackboard_state=blackboard_state,
                step_name=step_name,
                status_code=effective_status.value,
            )

        return StepExecutionResult(
            response=response,
            artifacts=artifacts,
            status_code=effective_status.value if effective_status is not None else None,
            auto_continue=auto_continue,
            events=events,
        )

    def _load_iteration_user_input_candidate(self, step_name: str) -> str:
        """Load raw user input before resume-token optimization."""
        if step_name in self.step_user_inputs:
            return self.step_user_inputs.pop(step_name)

        iteration_dir = self._get_iteration_dir(self.iteration)
        user_input_file = iteration_dir / "user_input.md"
        if user_input_file.exists():
            content = user_input_file.read_text(encoding="utf-8")
            if content.strip():
                return content

        if step_name == "plan" and self.iteration == 1:
            return ""
        return "workflow execute"

    def _apply_resume_user_input_to_candidate(self, step_name: str, candidate: str) -> str:
        agent_name = getattr(self, "_step_agent_name", None)
        if not agent_name or not hasattr(self, "agent_manager"):
            return candidate

        previous_data = self._load_previous_iteration_data()
        current_data = self._load_current_iteration_data()
        if not is_resume_iteration(
            iteration=self.iteration,
            previous_iteration_data=previous_data,
            current_iteration_data=current_data,
        ):
            return candidate

        prior_context = load_prior_run_context(
            iteration=self.iteration,
            previous_iteration_data=previous_data,
            current_iteration_data=current_data,
        )
        prior_cli, prior_session_id = prior_cli_and_session(prior_context)

        try:
            execution_config = self._resolve_execution_config_for_iteration(
                agent_name=agent_name,
                step_name=step_name,
            )
        except Exception:
            execution_config = self.agent_manager.get_agent(agent_name).config

        current_cli = (
            execution_config.cli.value
            if hasattr(execution_config.cli, "value")
            else str(execution_config.cli)
        )
        current_session_id = (
            execution_config.session_id if isinstance(execution_config.session_id, str) else None
        )

        return resolve_resume_user_input(
            candidate=candidate,
            prior_cli=prior_cli,
            prior_session_id=prior_session_id,
            current_cli=current_cli,
            current_session_id=current_session_id,
        )

    def _resolve_iteration_user_input(self, step_name: str) -> str:
        """Resolve user_input sent to agent for this step iteration."""
        candidate = self._load_iteration_user_input_candidate(step_name)
        return self._apply_resume_user_input_to_candidate(step_name, candidate)

    def _get_resolved_iteration_user_input(self, step_name: str) -> str:
        cached = self._resolved_iteration_user_input
        if cached is not None:
            return cached
        resolved = self._resolve_iteration_user_input(step_name)
        self._resolved_iteration_user_input = resolved
        return resolved

    def _apply_resume_to_runtime_context(
        self,
        runtime_context: Dict[str, str],
        step_name: str,
    ) -> Dict[str, str]:
        """Apply resume user-input rules to prompt runtime context."""
        updated = dict(runtime_context)
        if step_name in self.step_user_inputs:
            candidate = self.step_user_inputs.pop(step_name)
        elif updated.get("user_input"):
            candidate = updated["user_input"]
        else:
            candidate = self._load_iteration_user_input_candidate(step_name)

        resolved = self._apply_resume_user_input_to_candidate(step_name, candidate)
        self._resolved_iteration_user_input = resolved
        if resolved:
            updated["user_input"] = resolved
        else:
            updated.pop("user_input", None)

        # On resume, surface current input artifact paths so the agent
        # re-grounds on the current scope instead of inferring from prior work.
        previous_data = self._load_previous_iteration_data()
        current_data = self._load_current_iteration_data()
        if is_resume_iteration(
            iteration=self.iteration,
            previous_iteration_data=previous_data,
            current_iteration_data=current_data,
        ):
            artifact_lines = []
            declared = self._declared_prompt_input_placeholders(step_name)
            fallback_keys = ("develop_file", "spec_file", "plan_file", "feedback_file")
            keys = list(dict.fromkeys(declared + fallback_keys))
            for key in keys:
                if updated.get(key):
                    artifact_lines.append(f"- {key}: {updated[key]}")
            if artifact_lines:
                updated["resume_input_artifacts"] = "\n".join(artifact_lines)

        return updated

    def _declared_prompt_input_placeholders(self, step_name: str) -> tuple[str, ...]:
        """Return declared prompt-input keys for the current step in contract order."""
        steps = self.playbook.get("steps", {})
        step_def = steps.get(step_name) if isinstance(steps, dict) else None
        if not isinstance(step_def, dict):
            return ()
        skill_name = self._resolve_skill_name(step_def, self.iteration)
        contract = self._get_skill_loader().get_workflow_contract(skill_name)
        return tuple(mapping.placeholder for mapping in contract.prompt_inputs)

    def _detect_written_output_files(self) -> List[Path]:
        if self._current_output_file and self._current_output_file.exists():
            return [self._current_output_file]
        return []

    def _resolve_skill_name(self, step_def: Dict[str, Any], iteration: int) -> str:
        skill = step_def.get("skill")
        if isinstance(skill, str):
            return skill
        if isinstance(skill, dict):
            exact = skill.get(str(iteration))
            if exact:
                return str(exact)
            default = skill.get("default")
            if default:
                return str(default)
            numbered = sorted(skill.items(), key=lambda item: str(item[0]))
            if numbered:
                return str(numbered[0][1])
        raise ValueError("Step is missing skill configuration")

    def _resolve_phase_config_paths(self) -> tuple[Optional[Path], Optional[Path]]:
        local_path = None
        repo_path = None
        try:
            repo_root = get_repo_root()
            worktree_root = get_git_toplevel()
            repo_path = repo_root / ".cafe" / "phases.yaml"
            if self.issue_name:
                local_path = worktree_root / ".cafe" / "phases.yaml"
        except Exception:
            pass
        return local_path, repo_path

    def _resolve_step_phase_config(self, step_name: str):
        local_path, repo_path = self._resolve_phase_config_paths()
        return load_phase_step_model(
            step_name=step_name,
            local_path=local_path,
            repo_path=repo_path,
        )

    def _resolve_agent_name(self, step_name: str, step_def: Dict[str, Any]) -> str:
        role = str(step_def.get("role", "developer"))
        try:
            phase_resolution = self._resolve_step_phase_config(step_name)
            if phase_resolution.role and phase_resolution.role != role:
                raise ValueError(
                    f"phase config role mismatch for '{step_name}': expected '{role}', got '{phase_resolution.role}'"
                )
            if phase_resolution.name:
                return phase_resolution.name
        except ValueError as exc:
            raise ValueError(
                f"invalid phase config for '{step_name}' in '{step_name}': {exc}"
            ) from exc

        agent_name = self.role_agent_map.get(role)
        if agent_name:
            return agent_name

        playbook_role = self.playbook.get("roles", {}).get(role, {})
        if isinstance(playbook_role, dict) and playbook_role.get("default_agent"):
            return str(playbook_role["default_agent"])

        raise ValueError(f"Unsupported playbook role '{role}' for workflow execution")

    def _apply_step_agent_model(
        self, *, step_name: str, step_def: Dict[str, Any], agent_name: str
    ) -> None:
        model = self._resolve_step_model(step_name=step_name, step_def=step_def)
        self.agent_manager.get_agent(agent_name).config.model = model

    def _resolve_step_model(self, *, step_name: str, step_def: Dict[str, Any]) -> Optional[str]:
        # Phase-level config (worktree/repo/local) is authoritative for a step.
        try:
            phase_resolution = self._resolve_step_phase_config(step_name)
            expected_role = str(step_def.get("role", "developer"))
            if phase_resolution.role and phase_resolution.role != expected_role:
                raise ValueError(
                    f"phase config role mismatch for '{step_name}': expected '{expected_role}', got '{phase_resolution.role}'"
                )
            if phase_resolution.model:
                return phase_resolution.model
        except ValueError as exc:
            raise ValueError(
                f"invalid phase config for '{step_name}' in '{step_name}': {exc}"
            )

        role = str(step_def.get("role", "developer"))
        config = self.role_configs.get(role, {})
        if not isinstance(config, dict):
            return None

        # New crew.yaml format: per-phase models live under clis[].<phase>.
        # Delegate to the canonical chain resolver so this path stays in sync
        # with setup_agents() instead of silently ignoring the clis list.
        if isinstance(config.get("clis"), list):
            from cafe.utils.crew import normalize_role_config

            chain = normalize_role_config(config)
            if chain:
                return chain[0].resolve_model(step_name)
            return None

        phase_config = config.get(step_name)
        if isinstance(phase_config, dict):
            model = phase_config.get("model")
            if model:
                return str(model)

        model = config.get("model")
        return str(model) if model else None

    @staticmethod
    def _resolve_valid_intents(step_def: Dict[str, Any]) -> List[PhaseStatusCode]:
        valid = []
        known_values = {item.value for item in PhaseStatusCode}
        for code in step_def.get("valid_intents", []):
            if code in known_values:
                valid.append(PhaseStatusCode(code))
        return valid or list(PhaseStatusCode)

    @staticmethod
    def _normalize_allowed_tools(raw_tools: List[str]) -> List[str]:
        tool_name_map = {
            "Read": "read",
            "Edit": "edit",
            "Write": "write",
            "Grep": "grep",
            "Glob": "glob",
            "LS": "ls",
            "Ls": "ls",
            "Bash": "bash",
            "WebFetch": "web_fetch",
            "WebSearch": "web_search",
        }
        normalized = []
        for tool in raw_tools:
            if not tool:
                continue
            if "(" in tool:
                tool_name, remainder = tool.split("(", 1)
                normalized_name = tool_name_map.get(
                    tool_name, tool_name[:1].lower() + tool_name[1:]
                )
                normalized.append(f"{normalized_name}({remainder}")
                continue
            normalized.append(tool_name_map.get(tool, tool[:1].lower() + tool[1:]))
        return normalized

    def _build_allowed_tools(
        self,
        *,
        step_name: str,
        step_def: Dict[str, Any],
        output_file: Path,
        checklist_file: Path,
        questions_xml_file: Path,
    ) -> List[str]:
        allowed_tools = self._normalize_allowed_tools(step_def.get("allowed_tools", []))

        def add(tool: Optional[str]) -> None:
            if tool and tool not in allowed_tools:
                allowed_tools.append(tool)

        add("ls")

        # Always allow editing blackboard and baton so agents can hand off
        # without hitting permission denials (runtime also writes these, but
        # belt-and-suspenders prevents the agent from wasting tokens asking
        # for permission).
        add(f"edit({self._display_path(self.issue_dir / 'blackboard.json')})")
        add(f"edit({self._display_path(self.issue_dir / 'next_step.txt')})")

        if step_name in {"spec", "plan", "review", "pr"}:
            add(f"edit({self._display_path(output_file)})")
            add(f"edit({self._display_path(checklist_file)})")

        if step_on_declares(step_def, "need_clarification"):
            add(f"edit({self._display_path(questions_xml_file)})")

        if step_name == "review":
            add("web_fetch")
            add("web_search")
            add("bash(git log)")
            add("bash(git diff)")
            add("bash(git show)")
            add("bash(git status)")

        return allowed_tools

    @staticmethod
    def _is_baton_retry_user_input(user_input: str) -> bool:
        return "[BATON ERROR]" in user_input

    def _build_baton_retry_allowed_tools(self) -> List[str]:
        allowed_tools = ["read", "grep", "glob", "ls"]
        allowed_tools.append(f"edit({self._display_path(self.issue_dir / 'blackboard.json')})")
        allowed_tools.append(f"edit({self._display_path(self.issue_dir / 'next_step.txt')})")
        return allowed_tools

    def _build_context(
        self,
        *,
        step_name: str,
        step_def: Dict[str, Any],
        blackboard_state: BlackboardState,
        agent_name: str,
        output_file: Path,
    ) -> Dict[str, str]:
        role = str(step_def.get("role", "developer"))
        role_dir = {
            "pm": "pm",
            "reviewer": "reviewer",
            "writer": "writer",
            "editor": "editor",
            "researcher": "researcher",
            "ops": "ops",
        }.get(role, "developer")
        # 這條 playbook 實際可用的 to_step（= 所有 step 名 + 內建 user/done），
        # 與 baton 驗證器一致。注入 prompt 讓 agent 不會憑共用 skill 的範例（如 pr）
        # 猜出本 playbook 不存在的 step。
        playbook = getattr(self, "playbook", {})
        valid_to_steps = list(playbook.get("steps", {}).keys()) + ["user", "done"]
        # 本 step 依 intent 定義的下一步（含 _done → done 正規化），給 agent 明確指向。
        step_on = step_def.get("on", {}) if isinstance(step_def.get("on"), dict) else {}
        step_transitions = {
            str(k): ("done" if str(v) in ("_done", "done") else str(v)) for k, v in step_on.items()
        }
        context = {
            "agent_file": AgentManager.get_agent_file_path(agent_name, role_dir),
            "handoff_summary": getattr(blackboard_state, "handoff_summary", ""),
            "blackboard_digest": self._build_blackboard_digest(blackboard_state),
            "blackboard_path": self._display_path(self.issue_dir / "blackboard.json"),
            "next_step_path": self._display_path(self.issue_dir / "next_step.txt"),
            "output_file": self._display_path(output_file),
            "valid_to_steps": ", ".join(valid_to_steps),
            "step_transitions": ", ".join(f"{i}→{s}" for i, s in step_transitions.items()),
        }

        skill_name = self._resolve_skill_name(step_def, self.iteration)
        contract = self._get_skill_loader().get_workflow_contract(skill_name)
        input_artifacts = self._step_input_artifacts(step_def, blackboard_state)
        try:
            declared_inputs = resolve_prompt_inputs(contract, input_artifacts)
        except DeclaredArtifactError as exc:
            raise ValueError(
                f"Step {step_name!r}, skill {canonical_skill_name(skill_name)!r}: {exc}"
            ) from exc
        context.update(
            {
                placeholder: self._display_path(Path(path))
                for placeholder, path in declared_inputs.items()
            }
        )
        self._add_template_context(
            context=context,
            step_name=step_name,
            step_def=step_def,
            skill_name=skill_name,
            contract=contract,
        )

        if canonical_skill_name(skill_name) == "cafe-pr":
            base_branch = self._get_issue_config_value(self.issue_dir / "issue.yaml", "base_branch")
            resolved_base = str(base_branch or self.git_ops.get_default_base_branch())
            context["base_branch"] = resolved_base
            context["commits"] = self._get_current_branch_commits(
                self.git_ops,
                resolved_base,
            )

        return context

    @staticmethod
    def _step_input_artifacts(
        step_def: Dict[str, Any], blackboard_state: BlackboardState
    ) -> Dict[str, Any]:
        """Return only artifact records declared as inputs for this workflow step."""
        declared = step_def.get("input_artifacts")
        if declared is None:
            return dict(blackboard_state.artifacts)
        allowed = {str(name) for name in declared}
        return {
            name: artifact
            for name, artifact in blackboard_state.artifacts.items()
            if name in allowed
        }

    @classmethod
    def _build_blackboard_digest(cls, state: BlackboardState) -> str:
        """Serialize a small execution projection without unbounded event payloads."""

        def bounded(value: Any) -> str:
            text = str(value)
            if len(text) <= cls.BLACKBOARD_DIGEST_TEXT_LIMIT:
                return text
            return f"{text[: cls.BLACKBOARD_DIGEST_TEXT_LIMIT]}…"

        artifact_items = sorted(state.artifacts.items())
        selected_artifacts = artifact_items[-cls.BLACKBOARD_DIGEST_ARTIFACT_LIMIT :]
        artifacts = {
            name: {
                "version": entry.version,
                "updated_by": entry.updated_by,
                "path": bounded(entry.path),
            }
            for name, entry in selected_artifacts
        }
        recent_events = [
            {
                "timestamp": event.timestamp,
                "step": event.step,
                "event_type": event.event_type,
                "message": bounded(event.message),
            }
            for event in state.events[-cls.BLACKBOARD_DIGEST_EVENT_LIMIT :]
        ]
        digest = {
            "current_step": state.current_step,
            "playbook_id": state.playbook_id,
            "handoff_contract": (
                state.handoff_contract.to_dict()
                if state.handoff_contract is not None
                else None
            ),
            "artifacts": artifacts,
            "omitted_artifact_count": max(0, len(artifact_items) - len(selected_artifacts)),
            "recent_events": recent_events,
            "omitted_event_count": max(0, len(state.events) - len(recent_events)),
        }
        return json.dumps(digest, ensure_ascii=False, indent=2)

    def _generate_checklist(
        self,
        *,
        step_name: str,
        skill_name: str,
        agent_name: str,
        step_def: Dict[str, Any],
        blackboard_state: BlackboardState,
        checklist_file: Path,
        output_file: Path,
        questions_xml_file: Path,
    ) -> None:
        canonical_name = canonical_skill_name(skill_name)
        contract = self._get_skill_loader().get_workflow_contract(skill_name)
        input_artifacts = self._step_input_artifacts(step_def, blackboard_state)
        try:
            declared_inputs = resolve_prompt_inputs(contract, input_artifacts)
        except DeclaredArtifactError as exc:
            raise ValueError(f"Step {step_name!r}, skill {canonical_name!r}: {exc}") from exc

        previous_output = ""
        if self.iteration > 1:
            previous_output = self._display_path(
                self._get_versioned_file_path(step_name, self.iteration - 1, self.phase_dir)
            )
        context = {
            placeholder: self._display_path(Path(path))
            for placeholder, path in declared_inputs.items()
        }
        context.update(
            {
                "output_file": self._display_path(output_file),
                "questions_xml_file": self._display_path(questions_xml_file),
                "iteration": str(self.iteration),
                "previous_output_file": previous_output,
                "base_branch": str(
                    self._get_issue_config_value(self.issue_dir / "issue.yaml", "base_branch")
                    or self.git_ops.get_default_base_branch()
                ),
            }
        )
        feedback = bool(
            input_artifacts.get("review_feedback") or input_artifacts.get("pr_result")
        )
        compose_declared_checklist(
            skill_name=canonical_name,
            contract=contract,
            agent_name=agent_name,
            role=str(step_def.get("role", "developer")),
            checklist_file_path=checklist_file,
            iteration=self.iteration,
            context=context,
            artifacts=input_artifacts,
            feedback=feedback,
            template_mode=self._resolved_template_mode(step_name, step_def),
            template_file=self._resolved_template_file(step_name, step_def, canonical_name, contract),
        )

    def _resolved_template_mode(self, step_name: str, step_def: Dict[str, Any]) -> str:
        """Return the issue selection, playbook default, or auto for a declared catalog."""
        issue_value = self._get_issue_config_value(
            self.issue_dir / "issue.yaml", f"{step_name}.template"
        )
        return str(issue_value if issue_value is not None else step_def.get("template", "auto"))

    def _resolved_template_file(
        self,
        step_name: str,
        step_def: Dict[str, Any],
        skill_name: str,
        contract: SkillWorkflowContract,
    ) -> Optional[str]:
        """Resolve a named selection through the owning skill's catalog."""
        if contract.output_templates is None:
            return None
        selection = self._resolved_template_mode(step_name, step_def)
        if selection == "auto":
            return None
        manager = TemplateManager(
            template_type=contract.output_templates.catalog,
            skill_name=skill_name,
            skill_loader=self._get_skill_loader(),
        )
        template_file = manager.get_template_path(selection)
        if template_file is None:
            raise ValueError(
                f"Step {step_name!r} selected unknown template {selection!r} "
                f"from catalog {contract.output_templates.catalog!r}"
            )
        return self._display_path(template_file)

    def _template_allowed_directories_for(
        self,
        *,
        step_name: str,
        step_def: Dict[str, Any],
        skill_name: str,
        contract: SkillWorkflowContract,
    ) -> List[str]:
        """Grant read access to the catalog templates a step can select."""
        if contract.output_templates is None:
            return []
        manager = TemplateManager(
            template_type=contract.output_templates.catalog,
            skill_name=skill_name,
            skill_loader=self._get_skill_loader(),
        )
        selection = self._resolved_template_mode(step_name, step_def)
        if selection == "auto":
            return list(
                dict.fromkeys(
                    str(path.parent)
                    for name, _source in manager.list_templates()
                    if (path := manager.get_template_path(name)) is not None
                )
            )
        template_file = manager.get_template_path(selection)
        if template_file is None:
            raise ValueError(
                f"Step {step_name!r} selected unknown template {selection!r} "
                f"from catalog {contract.output_templates.catalog!r}"
            )
        return [str(template_file.parent)]

    def _add_template_context(
        self,
        *,
        context: Dict[str, str],
        step_name: str,
        step_def: Dict[str, Any],
        skill_name: str,
        contract: SkillWorkflowContract,
    ) -> None:
        """Expose only the declared template catalog or resolved selected file."""
        if contract.output_templates is None:
            return
        context["template_catalog"] = contract.output_templates.catalog
        template_file = self._resolved_template_file(step_name, step_def, skill_name, contract)
        if template_file is not None:
            context["template_file"] = template_file

    def _write_artifact_record(
        self,
        *,
        blackboard_state: BlackboardState,
        output_key: str,
        output_path: str,
        updated_by: str,
    ) -> None:
        previous = blackboard_state.artifacts.get(output_key)
        version = previous.version + 1 if previous else 1
        kind = ArtifactKind.WORKSPACE if output_key == "code" else ArtifactKind.DOCUMENT
        artifact = ArtifactEntry(
            name=output_key,
            kind=kind,
            version=version,
            updated_by=updated_by,
            path=output_path,
        )
        artifact_path = self._get_iteration_dir(self.iteration) / "artifact.json"
        artifact_path.write_text(
            json.dumps(artifact.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _should_validate_checklist(status_code: PhaseStatusCode) -> bool:
        return status_code in {
            PhaseStatusCode.CONFIRMED,
            PhaseStatusCode.AWAIT_AGENT,
            PhaseStatusCode.READY_FOR_REVIEW,
            PhaseStatusCode.CONFIRM_OUTPUT,
        }

    @staticmethod
    def _event_allows_auto_continue(event: Dict[str, Any]) -> bool:
        """Return whether a hook event represents user feedback for a paused step.

        Initial requirement collection also emits ``user_input_collected`` so
        the prompt can receive GitHub/manual issue text. That input is not a
        response to a clarification pause and must not suppress workflow
        pausing when the agent returns ``need_clarification``.
        """
        event_type = event.get("type")
        if event_type == "review_modification_requested":
            return True
        if event_type != "user_input_collected":
            return False
        return event.get("source") in {"questions_xml", "prompt", "user_input_file"}

    @staticmethod
    def _step_requires_status_code(step_name: str) -> bool:
        return step_name != "pr"

    @staticmethod
    def _resolve_handoff_intent(
        step_def: Dict[str, Any], status_code: PhaseStatusCode
    ) -> Optional[str]:
        if status_code in {
            PhaseStatusCode.READY_FOR_REVIEW,
            PhaseStatusCode.CONFIRM_OUTPUT,
        }:
            if step_on_declares(step_def, "confirm_output"):
                return "confirm_output"
            return "manual_handoff"
        if status_code == PhaseStatusCode.NEED_CLARIFICATION:
            return "need_clarification"
        if status_code == PhaseStatusCode.ALIGNMENT_CHECKPOINT:
            return "alignment_checkpoint"
        if status_code == PhaseStatusCode.NEED_PERMISSION:
            return "need_permission"
        if status_code == PhaseStatusCode.NO_CHANGES_NEEDED:
            return "no_changes_needed"
        return None

    def _agent_wrote_baton(self, step_name: str) -> bool:
        """Check whether the agent already wrote a valid baton (next_step.txt).

        A baton is considered agent-written when it exists, parses as a valid
        contract, targets a different step (from_step != to_step), and is
        attributed to the current step (or defaults to it under the new strict
        contract).
        """
        baton_path = self.issue_dir / "next_step.txt"
        if not baton_path.exists():
            return False
        try:
            payload = json.loads(baton_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return False
            contract = HandoffContract.from_dict_with_current_step(
                payload,
                current_step=step_name,
            )
            return (
                contract.from_step == step_name
                and contract.from_step != contract.to_step
                and contract.to_step
            )
        except Exception:
            return False

    def _write_status_transition_handoff(
        self,
        *,
        blackboard_state: BlackboardState,
        step_name: str,
        step_def: Dict[str, Any],
        response: str,
        status_code: PhaseStatusCode,
        auto_continue: bool,
    ) -> None:
        """Write the structured baton for status-based step contracts.

        Status codes are still accepted as the step-level result language for
        existing skills, but the workflow runtime should consume the baton
        written here instead of re-deriving transitions from agent text.
        """
        if step_name == "pr":
            return

        store = BlackboardStore(self.issue_dir)

        if status_code == PhaseStatusCode.NO_CHANGES_NEEDED:
            if self.interactive:
                # Interactive: always pause so UserInputCollector can show agree/disagree/chat.
                store.update_handoff_contract(
                    blackboard_state,
                    from_step=step_name,
                    to_owner=HandoffOwner.USER,
                    to_step="user",
                    intent=HandoffIntent.NO_CHANGES_NEEDED,
                    status_code=status_code.value,
                    source="workflow.status_transition_adapter",
                )
            else:
                # Non-interactive: follow playbook on.no_changes_needed routing.
                target = step_def.get("on", {}).get("no_changes_needed")
                if target and target != "user" and target in self.playbook.get("steps", {}):
                    store.update_handoff_contract(
                        blackboard_state,
                        from_step=step_name,
                        to_owner=HandoffOwner.AGENT,
                        to_step=target,
                        intent=HandoffIntent.AWAIT_AGENT,
                        status_code=status_code.value,
                        source="workflow.status_transition_adapter",
                    )
                else:
                    # Missing mapping or user target: backward-compatible pause.
                    store.update_handoff_contract(
                        blackboard_state,
                        from_step=step_name,
                        to_owner=HandoffOwner.USER,
                        to_step="user",
                        intent=HandoffIntent.NO_CHANGES_NEEDED,
                        status_code=status_code.value,
                        source="workflow.status_transition_adapter",
                    )
            return

        if not auto_continue and status_code.value in {
            PhaseStatusCode.READY_FOR_REVIEW.value,
            PhaseStatusCode.CONFIRM_OUTPUT.value,
            PhaseStatusCode.ALIGNMENT_CHECKPOINT.value,
            PhaseStatusCode.NEED_CLARIFICATION.value,
            PhaseStatusCode.NEED_PERMISSION.value,
        }:
            raw_intent = self._resolve_handoff_intent(step_def, status_code) or "manual_handoff"
            store.update_handoff_contract(
                blackboard_state,
                from_step=step_name,
                to_owner=HandoffOwner.USER,
                to_step="user",
                intent=HandoffIntent(raw_intent),
                status_code=status_code.value,
                source="workflow.status_transition_adapter",
            )
            return

        next_step = self._resolve_next_step_for_status(
            step_name=step_name,
            step_def=step_def,
            response=response,
            status_code=status_code,
        )
        if next_step is None:
            return

        if next_step in {"done", "_done"}:
            store.update_handoff_contract(
                blackboard_state,
                from_step=step_name,
                to_owner=HandoffOwner.DONE,
                to_step="done",
                intent=HandoffIntent.WORKFLOW_COMPLETE,
                status_code=status_code.value,
                source="workflow.status_transition_adapter",
            )
            return

        if next_step == "user":
            store.update_handoff_contract(
                blackboard_state,
                from_step=step_name,
                to_owner=HandoffOwner.USER,
                to_step="user",
                intent=HandoffIntent.MANUAL_HANDOFF,
                status_code=status_code.value,
                source="workflow.status_transition_adapter",
            )
            return

        if next_step not in self.playbook.get("steps", {}):
            return

        store.update_handoff_contract(
            blackboard_state,
            from_step=step_name,
            to_owner=HandoffOwner.AGENT,
            to_step=next_step,
            intent=HandoffIntent.AWAIT_AGENT,
            status_code=status_code.value,
            source="workflow.status_transition_adapter",
        )

    def _resolve_next_step_for_status(
        self,
        *,
        step_name: str,
        step_def: Dict[str, Any],
        response: str,
        status_code: PhaseStatusCode,
    ) -> Optional[str]:
        goto_target = self.generic_phase.extract_goto_target(response)
        if goto_target:
            allowed_targets = {str(target) for target in step_def.get("allowed_goto", [])}
            if goto_target in allowed_targets:
                return goto_target

        transitions = step_def.get("on", {})
        if not isinstance(transitions, dict):
            return None
        key = transition_map_key(status_code)
        target = transitions.get(key)
        if target is None:
            target = transitions.get("default")
        return str(target) if target else None

    def _artifact_path(self, blackboard_state: BlackboardState, name: str) -> Optional[str]:
        entry = blackboard_state.artifacts.get(name)
        if entry:
            return self._display_path(Path(entry.path))
        return None

    def _artifact_or_latest_path(
        self,
        blackboard_state: BlackboardState,
        artifact_name: str,
        phase_name: str,
    ) -> Optional[str]:
        path = self._artifact_path(blackboard_state, artifact_name)
        if path:
            return path
        latest = self._get_latest_versioned_file(phase_name, self.issue_dir / phase_name)
        if latest:
            return self._display_path(latest)
        return None

    def _ensure_output_file_initialized(self, step_name: str, output_file: Path) -> None:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        if output_file.exists():
            return
        if step_name == "pr":
            output_file.write_text(
                "# [Your PR Title Here]\n\n## Summary\n\n## Changes\n\n## Test Plan\n",
                encoding="utf-8",
            )
            return
        output_file.write_text("", encoding="utf-8")

    @staticmethod
    def _declared_capability_ids(step_def: Dict[str, Any]) -> List[str]:
        raw = step_def.get("capability_requests") or []
        if not isinstance(raw, list):
            return []
        return [str(item).strip() for item in raw if str(item).strip()]

    def _effective_capability_ids(self, step_name: str, step_def: Dict[str, Any]) -> List[str]:
        declared = self._declared_capability_ids(step_def)
        if declared:
            return declared
        if step_name == "pr":
            return [CAPABILITY_PR_PUBLISH_ID]
        return []

    def _write_capability_request(
        self,
        *,
        output_file: Path,
        capability_request_file: Path,
        capability_ids: List[str],
    ) -> None:
        requests = [
            self._build_capability_request(
                output_file=output_file,
                capability_id=capability_id,
            )
            for capability_id in capability_ids
        ]
        payload: Dict[str, Any]
        if len(requests) == 1:
            payload = requests[0]
        else:
            payload = {"requests": requests}
        capability_request_file.write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _write_publish_request(
        self,
        *,
        output_file: Path,
        publish_request_file: Path,
    ) -> None:
        publish_request_file.write_text(
            json.dumps(
                self._build_capability_request(
                    output_file=output_file,
                    capability_id=CAPABILITY_PR_PUBLISH_ID,
                ),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _build_publish_request(self, *, output_file: Path) -> Dict[str, Any]:
        return self._build_capability_request(
            output_file=output_file,
            capability_id=CAPABILITY_PR_PUBLISH_ID,
        )

    def _build_capability_request(
        self,
        *,
        output_file: Path,
        capability_id: str,
    ) -> Dict[str, Any]:
        if capability_id != CAPABILITY_PR_PUBLISH_ID:
            return {
                "capability": capability_id,
                "args": {},
                "permissions": {},
            }

        base_branch = self._get_issue_config_value(self.issue_dir / "issue.yaml", "base_branch")
        resolved_base = str(base_branch or self.git_ops.get_default_base_branch())
        return {
            "capability": CAPABILITY_PR_PUBLISH_ID,
            "args": {
                "output": self._repo_relative_path(output_file),
                "base": resolved_base,
            },
            "permissions": {
                "network": ["github.com", "api.github.com"],
                "writes": [
                    ".git",
                    self._repo_relative_path(self.issue_dir),
                ],
            },
        }

    def _repo_relative_path(self, path: Path) -> str:
        repo_root = self._resolve_repo_root()
        resolved = path.resolve()
        try:
            return str(resolved.relative_to(repo_root))
        except ValueError:
            return str(resolved)

    def _resolve_repo_root(self) -> Path:
        try:
            repo_root = self.git_ops.get_repo_root()
        except Exception:
            repo_root = Path.cwd()
        return Path(repo_root).resolve()

    @staticmethod
    def _display_path(path: Path) -> str:
        try:
            return to_cwd_relative_path(path)
        except (ValueError, OSError):
            return str(path)
