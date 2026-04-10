"""Direct playbook step execution through GenericPhase."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from cafe.agents.manager import AgentManager
from cafe.core.blackboard import ArtifactEntry, ArtifactKind, BlackboardState, BlackboardStore
from cafe.core.playbook_runner import StepExecutionResult
from cafe.core.git import GitOperations
from cafe.core.phase import Phase
from cafe.core.status_codes import PhaseStatusCode, generate_status_code_prompt
from cafe.phases.generic_phase import GenericPhase
from cafe.utils.checklist_generator import (
    generate_develop_checklist,
    generate_plan_checklist,
    generate_pr_checklist,
    generate_review_checklist,
    generate_spec_checklist,
)
from cafe.utils.git_utils import to_cwd_relative_path


class GenericWorkflowStepExecutor(Phase):
    """Execute one playbook step without shelling out to legacy CLI commands."""

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
        step_user_inputs: Optional[Dict[str, str]] = None,
        interactive: bool = False,
    ) -> None:
        self.interactive = interactive
        self.issue_dir = issue_dir
        self.issue_name = issue_name
        self.playbook = playbook
        self.generic_phase = generic_phase
        self.agent_manager = agent_manager
        self.git_ops = git_ops
        self.role_agent_map = role_agent_map
        self.step_user_inputs = dict(step_user_inputs or {})
        self.phase_name = ""
        self.phase_dir = issue_dir
        self.iteration = 0
        self._current_output_file: Optional[Path] = None

    def execute(self) -> Any:
        raise NotImplementedError("GenericWorkflowStepExecutor only supports execute_step()")

    def execute_step(
        self,
        step_name: str,
        step_def: Dict[str, Any],
        blackboard_state: BlackboardState,
    ) -> StepExecutionResult:
        self.phase_name = step_name
        self.phase_dir = self.issue_dir / step_name
        self.phase_dir.mkdir(parents=True, exist_ok=True)

        self.iteration = self._get_next_iteration_number(step_name, self.phase_dir)
        iteration_dir = self._get_iteration_dir(self.iteration)
        iteration_dir.mkdir(parents=True, exist_ok=True)

        output_file = self._get_versioned_file_path(step_name, self.iteration, self.phase_dir)
        checklist_file = iteration_dir / "checklist.md"
        questions_xml_file = iteration_dir / "questions.xml"
        self._current_output_file = output_file

        if self.iteration > 1:
            self._copy_previous_version(step_name, self.iteration, self.phase_dir)
        self._ensure_output_file_initialized(step_name, output_file)

        skill_name = self._resolve_skill_name(step_def, self.iteration)
        valid_status_codes = self._resolve_valid_status_codes(step_def)
        agent_name = self._resolve_agent_name(step_def)
        agent_cli = self.agent_manager.get_agent(agent_name).config.cli
        skill_invocation = self.generic_phase.prepare_skill(
            skill_name=skill_name,
            agent_cli=agent_cli,
        )
        context = self._build_context(
            step_name=step_name,
            step_def=step_def,
            blackboard_state=blackboard_state,
            agent_name=agent_name,
            output_file=output_file,
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
        allowed_tools = self._normalize_allowed_tools(step_def.get("allowed_tools", []))
        phase_specific_data = {
            "step_name": step_name,
            "skill_name": skill_name,
            "playbook_id": self.playbook.get("playbook", {}).get("id"),
        }

        def run_agent(prompt: str) -> str:
            last_prompt[:] = [prompt]
            response, _ = self._execute_agent_iteration(
                agent_name=agent_name,
                prompt=prompt,
                user_input=self.step_user_inputs.get(step_name, "workflow execute"),
                valid_status_codes=valid_status_codes,
                require_status_code=False,
                allowed_tools=allowed_tools,
                phase_specific_data=phase_specific_data,
            )
            return response

        execution = self.generic_phase.execute(
            skill_name=skill_name,
            step_def=step_def,
            agent_executor=run_agent,
            skill_invocation=skill_invocation,
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
                "blackboard_state": blackboard_state,
            },
        )

        response = execution.response
        status_code = execution.status_code

        agent_was_invoked = bool(last_prompt)
        if agent_was_invoked and status_code is not None and self._should_validate_checklist(status_code):
            response, validated_status, validation_passed = self._validate_and_retry_checklist_completion(
                agent_name=agent_name,
                prompt=last_prompt[0] if last_prompt else "",
                user_input=self.step_user_inputs.get(step_name, "workflow execute"),
                valid_status_codes=valid_status_codes,
                allowed_tools=allowed_tools,
                max_retries=3,
            )
            if validation_passed and validated_status is not None:
                status_code = validated_status

        self._persist_final_status(status_code)

        output_key = str(step_def.get("output_artifact", step_name))
        artifacts: Dict[str, str] = {}
        if output_file.exists():
            output_path = str(output_file)
            artifacts[output_key] = output_path
            self._write_artifact_record(
                blackboard_state=blackboard_state,
                output_key=output_key,
                output_path=output_path,
                updated_by=step_name,
            )

        auto_continue = any(
            event.get("type") in {"user_input_collected", "review_modification_requested"}
            for event in execution.events
            if isinstance(event, dict)
        )
        if self.interactive and status_code in {
            PhaseStatusCode.NEED_CLARIFICATION,
            PhaseStatusCode.READY_FOR_REVIEW,
        }:
            auto_continue = True

        return StepExecutionResult(
            response=response,
            artifacts=artifacts,
            status_code=status_code.value if status_code is not None else None,
            auto_continue=auto_continue,
        )

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

    def _resolve_agent_name(self, step_def: Dict[str, Any]) -> str:
        role = str(step_def.get("role", "developer"))
        agent_name = self.role_agent_map.get(role)
        if agent_name:
            return agent_name

        playbook_role = self.playbook.get("roles", {}).get(role, {})
        if isinstance(playbook_role, dict) and playbook_role.get("default_agent"):
            return str(playbook_role["default_agent"])

        raise ValueError(f"Unsupported playbook role '{role}' for workflow execution")

    @staticmethod
    def _resolve_valid_status_codes(step_def: Dict[str, Any]) -> List[PhaseStatusCode]:
        valid = []
        known_values = {item.value for item in PhaseStatusCode}
        for code in step_def.get("valid_status_codes", []):
            if code in known_values:
                valid.append(PhaseStatusCode(code))
        return valid or list(PhaseStatusCode)

    @staticmethod
    def _normalize_allowed_tools(raw_tools: List[str]) -> List[str]:
        normalized = []
        for tool in raw_tools:
            if not tool:
                continue
            normalized.append(tool[:1].lower() + tool[1:])
        return normalized

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
        role_dir = {"pm": "pm", "reviewer": "reviewer"}.get(role, "developer")
        context = {
            "agent_file": AgentManager.get_agent_file_path(agent_name, role_dir),
            "blackboard_digest": BlackboardStore(self.issue_dir).generate_digest(
                blackboard_state,
                for_step=step_name,
            ),
            "blackboard_path": self._display_path(self.issue_dir / "blackboard.json"),
            "next_step_path": self._display_path(self.issue_dir / "next_step.txt"),
            "output_file": self._display_path(output_file),
            "status_code_instruction": generate_status_code_prompt(
                self._resolve_valid_status_codes(step_def),
                {},
            ),
        }

        for artifact_name in step_def.get("input_artifacts", []):
            entry = blackboard_state.artifacts.get(str(artifact_name))
            if not entry:
                continue
            artifact_path = self._display_path(Path(entry.path))
            if artifact_name == "spec":
                context["spec_file"] = artifact_path
            elif artifact_name == "plan":
                context["plan_file"] = artifact_path
            elif artifact_name == "code":
                context["develop_file"] = artifact_path
            elif artifact_name == "review_feedback":
                context["feedback_file"] = artifact_path

        if "spec_file" not in context:
            latest_spec = self._get_latest_versioned_file("spec", self.issue_dir / "spec")
            if latest_spec:
                context["spec_file"] = self._display_path(latest_spec)
        if "plan_file" not in context:
            latest_plan = self._get_latest_versioned_file("plan", self.issue_dir / "plan")
            if latest_plan:
                context["plan_file"] = self._display_path(latest_plan)

        if self._resolve_skill_name(step_def, self.iteration) == "pr":
            base_branch = self._get_issue_config_value(self.issue_dir / "issue.yaml", "base_branch")
            context["commits"] = self._get_current_branch_commits(
                self.git_ops,
                str(base_branch or self.git_ops.get_main_branch()),
            )

        return context

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
        output_display = self._display_path(output_file)
        questions_display = self._display_path(questions_xml_file)
        spec_path = self._artifact_or_latest_path(blackboard_state, "spec", "spec")
        plan_path = self._artifact_or_latest_path(blackboard_state, "plan", "plan")
        review_feedback = self._artifact_path(blackboard_state, "review_feedback")

        if skill_name in {"spec_first", "spec_revise"}:
            prev_spec = None
            if self.iteration > 1:
                prev_spec_file = self._get_versioned_file_path(step_name, self.iteration - 1, self.phase_dir)
                prev_spec = self._display_path(prev_spec_file)
            generate_spec_checklist(
                iteration=self.iteration,
                agent_name=agent_name,
                current_spec_file=output_display,
                prev_spec_file=prev_spec,
                checklist_file_path=checklist_file,
                questions_xml_file=questions_display,
            )
            return

        if skill_name == "plan":
            if not spec_path:
                raise ValueError("Plan step requires spec artifact")
            prev_plan = None
            if self.iteration > 1:
                prev_plan_file = self._get_versioned_file_path(step_name, self.iteration - 1, self.phase_dir)
                prev_plan = self._display_path(prev_plan_file)
            generate_plan_checklist(
                agent_name=agent_name,
                plan_file_path=output_display,
                spec_file_path=spec_path,
                checklist_file_path=checklist_file,
                iteration=self.iteration,
                prev_plan_file=prev_plan,
                questions_xml_file=questions_display,
            )
            return

        if skill_name == "develop":
            if not spec_path or not plan_path:
                raise ValueError("Develop step requires spec and plan artifacts")
            generate_develop_checklist(
                agent_name=agent_name,
                spec_file_path=spec_path,
                plan_file_path=plan_path,
                develop_file=None,
                checklist_file_path=checklist_file,
                correction_mode=review_feedback is not None,
                feedback_file_path=review_feedback,
                output_file=output_display,
                questions_xml_file=questions_display,
            )
            return

        if skill_name == "review":
            if not spec_path:
                raise ValueError("Review step requires spec artifact")
            base_branch = self._get_issue_config_value(self.issue_dir / "issue.yaml", "base_branch")
            generate_review_checklist(
                agent_name=agent_name,
                spec_file_path=spec_path,
                plan_file_path=plan_path,
                review_file_path=output_display,
                base_branch=str(base_branch or self.git_ops.get_main_branch()),
                checklist_file_path=checklist_file,
            )
            return

        if skill_name == "pr":
            if not spec_path or not plan_path:
                raise ValueError("PR step requires spec and plan artifacts")
            prev_pr = None
            if self.iteration > 1:
                prev_pr_file = self._get_versioned_file_path(step_name, self.iteration - 1, self.phase_dir)
                prev_pr = self._display_path(prev_pr_file)
            generate_pr_checklist(
                agent_name=agent_name,
                spec_file_path=spec_path,
                plan_file_path=plan_path,
                pr_file=output_display,
                checklist_file_path=checklist_file,
                iteration=self.iteration,
                prev_pr_file=prev_pr,
            )
            return

        checklist_file.write_text("", encoding="utf-8")

    def _persist_final_status(self, status_code: Optional[PhaseStatusCode]) -> None:
        context_file = self._get_iteration_dir(self.iteration) / "context.json"
        if context_file.exists():
            context_data = json.loads(context_file.read_text(encoding="utf-8"))
            context_data["status_code"] = status_code.value if status_code is not None else None
            context_file.write_text(json.dumps(context_data, ensure_ascii=False, indent=2), encoding="utf-8")
        if status_code is not None:
            self._save_progress(status_code)

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
        return status_code in {PhaseStatusCode.CONFIRMED, PhaseStatusCode.READY_FOR_REVIEW}

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
    def _display_path(path: Path) -> str:
        try:
            return to_cwd_relative_path(path)
        except (ValueError, OSError):
            return str(path)
