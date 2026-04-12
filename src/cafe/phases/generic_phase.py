"""Generic phase for skill-driven execution."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from cafe.core.hooks import BUILTIN_HOOKS, HookResult
from cafe.core.questions_schema import validate_questions_xml
from cafe.core.status_codes import PhaseStatusCode, StatusCodeParser
from cafe.skills.loader import SkillLoader
from cafe.skills.native_bridge import NativeSkillBridge
from cafe.core.types import AgentCLI


AgentExecutor = Callable[[str], str]


@dataclass
class GenericPhaseExecution:
    """Result of one generic phase execution."""

    response: str
    status_code: Optional[PhaseStatusCode]
    goto_target: Optional[str]
    context_updates: Dict[str, str] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)
    artifact_ready: bool = True
    published: bool = False


class GenericPhase:
    """Build prompts from skill content and run lifecycle hooks."""

    GOTO_PATTERN = re.compile(r"CAFE_GOTO\s*:\s*([a-zA-Z0-9_-]+)")

    def __init__(
        self,
        skill_loader: SkillLoader,
        *,
        hook_registry: Optional[Dict[str, type]] = None,
        skill_bridge: Optional[NativeSkillBridge] = None,
    ) -> None:
        self.skill_loader = skill_loader
        self.skill_bridge = skill_bridge or NativeSkillBridge(skill_loader)
        self.hook_registry = dict(BUILTIN_HOOKS)
        if hook_registry:
            self.hook_registry.update(hook_registry)

    def prepare_skill(self, *, skill_name: str, agent_cli: AgentCLI) -> str:
        """Install one skill for the target CLI and return its invocation syntax."""
        self.skill_bridge.install_skill(skill_name, agent_cli)
        return self.skill_bridge.get_invocation(skill_name, agent_cli)

    def prepare_skills(self, *, skill_names: list[str], agent_cli: AgentCLI) -> list[str]:
        """Install a list of skills for the target CLI and return invocation syntax."""
        self.skill_bridge.install_skills(skill_names, agent_cli)
        return [self.skill_bridge.get_invocation(name, agent_cli) for name in skill_names]

    def build_prompt(
        self,
        *,
        skill_name: str,
        skill_invocation: str,
        shared_skill_invocations: Optional[List[str]] = None,
        context: Optional[Dict[str, str]] = None,
        output_file: Optional[Path] = None,
        checklist_file: Optional[Path] = None,
        questions_xml_file: Optional[Path] = None,
    ) -> str:
        lines = []
        runtime_files: list[str] = []
        runtime_context: list[str] = []

        if shared_skill_invocations:
            lines.append("Shared skills:")
            lines.extend(f"- {invocation}" for invocation in shared_skill_invocations)
            lines.append("")
        lines.extend(
            [
                f"Phase skill: {skill_invocation}",
                "",
            ]
        )

        if output_file is not None:
            runtime_files.append(f"output_file={output_file}")
        if checklist_file is not None:
            runtime_files.append(f"checklist_file={checklist_file}")
        if questions_xml_file is not None:
            runtime_files.append(f"questions_file={questions_xml_file}")
        if context and context.get("blackboard_path"):
            runtime_files.append(f"blackboard_file={context['blackboard_path']}")
        if context and context.get("next_step_path"):
            runtime_files.append(f"next_step_file={context['next_step_path']}")

        if runtime_files:
            lines.extend(["Runtime files:"])
            lines.extend(runtime_files)
            lines.append("")

        if context and context.get("handoff_summary"):
            runtime_context.extend(
                [
                    "Latest workflow handoff from blackboard:",
                    context["handoff_summary"],
                    "Treat this handoff as the highest-priority current request unless current files prove it is already completed.",
                ]
            )
        if context and context.get("user_input"):
            runtime_context.extend(["Current user input for this iteration:", context["user_input"]])

        if runtime_context:
            lines.extend(["Runtime context:"])
            lines.extend(runtime_context)
            lines.append("")

        if checklist_file is not None:
            lines.append("Do NOT return a status code until ALL checklist items are marked as [x].")

        return "\n".join(lines).strip()

    @classmethod
    def extract_goto_target(cls, response: str) -> Optional[str]:
        match = cls.GOTO_PATTERN.search(response)
        if not match:
            return None
        return match.group(1)

    def parse_response(
        self,
        *,
        response: str,
        valid_status_codes: List[PhaseStatusCode],
    ) -> Tuple[Optional[PhaseStatusCode], Optional[str]]:
        status = StatusCodeParser.extract(response, valid_codes=valid_status_codes)
        goto_target = self.extract_goto_target(response)
        return status, goto_target

    def validate_clarification_output(
        self,
        *,
        status_code: Optional[PhaseStatusCode],
        questions_xml_file: Path,
    ) -> None:
        if status_code != PhaseStatusCode.NEED_CLARIFICATION:
            return
        if not questions_xml_file.exists():
            raise ValueError(
                f"Status is {PhaseStatusCode.NEED_CLARIFICATION.value} but questions.xml is missing: {questions_xml_file}"
            )
        if not validate_questions_xml(questions_xml_file):
            raise ValueError(f"questions.xml format is invalid: {questions_xml_file}")

    def execute(
        self,
        *,
        skill_name: str,
        step_def: Dict[str, Any],
        agent_executor: AgentExecutor,
        skill_invocation: str,
        shared_skill_invocations: Optional[List[str]] = None,
        context: Optional[Dict[str, str]] = None,
        output_file: Optional[Path] = None,
        checklist_file: Optional[Path] = None,
        questions_xml_file: Optional[Path] = None,
        hook_context: Optional[Dict[str, Any]] = None,
        max_retries: int = 3,
    ) -> GenericPhaseExecution:
        runtime_context = dict(context or {})
        events: List[Dict[str, Any]] = []
        artifact_ready = True
        hook_kwargs = dict(hook_context or {})

        before = self._run_hook_stage(
            "before_execute",
            step_def=step_def,
            skill_name=skill_name,
            context=runtime_context,
            **hook_kwargs,
        )
        runtime_context.update(before.context_updates)
        events.extend(before.events)
        artifact_ready = artifact_ready and before.artifact_ready
        if not before.continue_pipeline:
            return GenericPhaseExecution(
                response="",
                status_code=before.override_status_code,
                goto_target=None,
                context_updates=runtime_context,
                events=events,
                artifact_ready=artifact_ready,
                published=False,
            )

        prepared = self._run_hook_stage(
            "prepare_input",
            step_def=step_def,
            skill_name=skill_name,
            context=runtime_context,
            **hook_kwargs,
        )
        runtime_context.update(prepared.context_updates)
        events.extend(prepared.events)
        artifact_ready = artifact_ready and prepared.artifact_ready
        if not prepared.continue_pipeline:
            return GenericPhaseExecution(
                response="",
                status_code=prepared.override_status_code,
                goto_target=None,
                context_updates=runtime_context,
                events=events,
                artifact_ready=artifact_ready,
                published=False,
            )

        response = ""
        status_code: Optional[PhaseStatusCode] = None
        goto_target: Optional[str] = None
        attempt = 0
        while True:
            prompt = self.build_prompt(
                skill_name=skill_name,
                skill_invocation=skill_invocation,
                shared_skill_invocations=shared_skill_invocations,
                context=runtime_context,
                output_file=output_file,
                checklist_file=checklist_file,
                questions_xml_file=questions_xml_file,
            )
            response = agent_executor(prompt)
            valid_codes = [
                PhaseStatusCode(code)
                for code in step_def.get("valid_status_codes", [])
                if code in {item.value for item in PhaseStatusCode}
            ] or list(PhaseStatusCode)
            status_code, goto_target = self.parse_response(
                response=response,
                valid_status_codes=valid_codes,
            )
            if questions_xml_file is not None:
                self.validate_clarification_output(
                    status_code=status_code,
                    questions_xml_file=questions_xml_file,
                )

            after = self._run_hook_stage(
                "after_execute",
                step_def=step_def,
                skill_name=skill_name,
                context=runtime_context,
                response=response,
                status_code=status_code,
                goto_target=goto_target,
                **hook_kwargs,
            )
            runtime_context.update(after.context_updates)
            events.extend(after.events)
            artifact_ready = artifact_ready and after.artifact_ready
            if after.override_status_code is not None:
                status_code = after.override_status_code

            if not after.retry_requested:
                break
            attempt += 1
            if attempt >= max_retries:
                raise RuntimeError("GenericPhase exceeded max retry attempts")

        published = False
        if artifact_ready:
            publish = self._run_hook_stage(
                "publish_output",
                step_def=step_def,
                skill_name=skill_name,
                context=runtime_context,
                response=response,
                status_code=status_code,
                goto_target=goto_target,
                **hook_kwargs,
            )
            runtime_context.update(publish.context_updates)
            events.extend(publish.events)
            published = publish.continue_pipeline
            if publish.override_status_code is not None:
                status_code = publish.override_status_code

        return GenericPhaseExecution(
            response=response,
            status_code=status_code,
            goto_target=goto_target,
            context_updates=runtime_context,
            events=events,
            artifact_ready=artifact_ready,
            published=published,
        )

    def _run_hook_stage(
        self,
        stage: str,
        **kwargs: Any,
    ) -> HookResult:
        hook_names = kwargs["step_def"].get("hooks", {}).get(stage, [])
        aggregate = HookResult()

        for hook_name in hook_names:
            hook_cls = self.hook_registry.get(str(hook_name))
            if hook_cls is None:
                raise ValueError(f"Unknown hook '{hook_name}' in stage '{stage}'")

            hook = hook_cls()
            result = hook.run(stage=stage, **kwargs)
            aggregate.context_updates.update(result.context_updates)
            aggregate.events.extend(result.events)
            aggregate.artifact_ready = aggregate.artifact_ready and result.artifact_ready
            aggregate.retry_requested = aggregate.retry_requested or result.retry_requested
            if result.override_status_code is not None:
                aggregate.override_status_code = result.override_status_code
            if not result.continue_pipeline:
                aggregate.continue_pipeline = False
                break

        return aggregate
