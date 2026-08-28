"""Generic phase for skill-driven execution."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import yaml

from cafe.catalogs.resolver import global_catalog_lock
from cafe.core.blackboard import BlackboardState, BlackboardStore, HandoffIntent
from cafe.core.capabilities import (
    CAPABILITY_ISSUE_COMMENT_ID,
    default_capability_definition_dirs,
    load_capability_registry,
    run_capability_request,
)
from cafe.core.execution_boundary import (
    EffectiveBoundary,
    ExecutionClass,
    ScriptLaunchRequest,
    TrustSource,
    snapshot_script_tree,
)
from cafe.core.hooks import BUILTIN_HOOKS, HookResult
from cafe.core.hooks.script_schema import validate_script_args_schema
from cafe.core.questions_schema import validate_questions_xml
from cafe.core.sandbox_execution import MIGRATION_GUIDANCE, SandboxExecutor
from cafe.core.status_codes import (
    PhaseStatusCode,
    StatusCodeParser,
    effective_step_status_codes,
)
from cafe.core.types import AgentCLI
from cafe.skills.loader import SkillLoader, canonical_skill_name
from cafe.skills.native_bridge import NativeSkillBridge

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
    agent_invoked: bool = False


class GenericPhase:
    """Build prompts from skill content and run lifecycle hooks."""

    GOTO_PATTERN = re.compile(r"GOTO\s*:\s*([a-zA-Z0-9_-]+)")
    PLACEHOLDER_PATTERN = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
    SCRIPT_HOOK_STAGES = {"before_execute", "after_execute"}
    _CONFIRMED_ARTIFACT_SYNC_HOOK = object()

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

    def prepare_skill(
        self,
        *,
        skill_name: str,
        agent_cli: AgentCLI,
        context: Optional[Dict[str, str]] = None,
    ) -> str:
        """Install one skill for the target CLI and return its invocation syntax."""
        installed_dir = self.skill_bridge.install_skill(skill_name, agent_cli, context=context)
        contract = self.skill_loader.get_workflow_contract(skill_name)
        prompt_references = self._render_prompt_references(
            skill_name=skill_name,
            references=contract.prompt_references,
            context=context or {},
        )
        resolved_inputs = [
            (mapping.placeholder, str((context or {})[mapping.placeholder]))
            for mapping in contract.prompt_inputs
            if (context or {}).get(mapping.placeholder)
        ]
        if prompt_references or resolved_inputs:
            skill_file = installed_dir / "SKILL.md"
            rendered = skill_file.read_text(encoding="utf-8")
            for placeholder, content in prompt_references.items():
                rendered = rendered.replace(f"{{{placeholder}}}", content)
            if resolved_inputs:
                rendered += "\n\n## Workflow Inputs\n" + "\n".join(
                    f"- {placeholder}: {path}" for placeholder, path in resolved_inputs
                )
            skill_file.write_text(rendered, encoding="utf-8")
        return self.skill_bridge.get_invocation(skill_name, agent_cli)

    def _render_prompt_references(
        self,
        *,
        skill_name: str,
        references: Dict[str, str],
        context: Dict[str, str],
    ) -> Dict[str, str]:
        """Render named prompt sections only when every referenced input is available."""
        resolved: Dict[str, str] = {}
        for placeholder, reference in references.items():
            content = self.skill_loader.get_reference(skill_name, reference)
            names = self.PLACEHOLDER_PATTERN.findall(content)
            if not all(context.get(name) for name in names):
                resolved[placeholder] = ""
                continue
            for name in set(names):
                content = content.replace(f"{{{name}}}", str(context[name]))
            resolved[placeholder] = content
        return resolved

    def prepare_skills(
        self,
        *,
        skill_names: list[str],
        agent_cli: AgentCLI,
        context: Optional[Dict[str, str]] = None,
    ) -> list[str]:
        """Install a list of skills for the target CLI and return invocation syntax."""
        return [
            self.prepare_skill(skill_name=name, agent_cli=agent_cli, context=context)
            for name in skill_names
        ]

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

        baton_intents = (context.get("valid_baton_intents", "") if context else "") or ", ".join(
            intent.value for intent in HandoffIntent
        )
        runtime_context.append("Baton contract (single source of truth):")
        runtime_context.append(
            "- write next_step_file as JSON with exactly these required fields: "
            '{"version":1,"to_owner":"<agent|user|done>",'
            '"to_step":"<target>","intent":"<intent>"}'
        )
        runtime_context.append(
            "- never write blackboard_file; the runtime derives audit metadata "
            "and updates the blackboard"
        )
        runtime_context.append(f"- valid intent values: [{baton_intents}]")
        # 列出本 playbook 實際合法的 to_step，避免 agent 沿用共用 skill 範例裡的 step
        # （如 pr）而寫出此 playbook 不存在的目標導致 baton 被拒。
        if context and context.get("valid_to_steps"):
            runtime_context.append(
                f"- valid to_step values: [{context['valid_to_steps']}] "
                "— use ONLY these; this playbook has no other steps (e.g. do not assume 'pr')"
            )
        if context and context.get("step_transitions"):
            runtime_context.append(
                f"- this step's defined transitions (intent→to_step): {context['step_transitions']}"
            )
        runtime_context.append(
            "- when asking user questions, handoff must be to_owner='user', to_step='user', intent='need_clarification'"
        )
        runtime_context.append(
            "- stay within this prompt's listed shared skills + phase skill; do not invoke external workflow-driving skills (e.g. use-cafe-workflow)"
        )
        if context and context.get("blackboard_digest"):
            runtime_context.extend(
                [
                    "Bounded blackboard digest:",
                    context["blackboard_digest"],
                    "Use this digest for initial workflow grounding. Do not read the full blackboard file; it is an unbounded audit history and may exceed the agent context window.",
                    "If a concrete conflict requires older history, query only the specific field or event needed, and do not print event data payloads wholesale.",
                ]
            )

        if context and context.get("handoff_summary"):
            runtime_context.extend(
                [
                    "Latest workflow handoff from blackboard:",
                    context["handoff_summary"],
                    "Treat this handoff as the highest-priority current request unless current files prove it is already completed.",
                    "Before finishing this step, verify whether the requested state change has actually happened in files or external state relevant to this phase.",
                    "If the handoff asks for a retry, re-run, re-sync, or re-open action, do not treat an old artifact or a closed external object as completion.",
                ]
            )
            if canonical_skill_name(skill_name) == "cafe-pr":
                runtime_context.extend(
                    [
                        "For the PR phase, completion is local-only: finish the PR artifact and checklist, then update the workflow baton.",
                        "Do not wait for, verify, or require a remote GitHub branch/PR before updating the workflow baton.",
                        "Remote PR publish happens later in the host-side publish_output hook.",
                    ]
                )
        if context and context.get("resume_input_artifacts"):
            runtime_context.extend(
                [
                    "Current resume scope (declared step inputs):",
                    "Read every listed current source before selecting work.",
                    context["resume_input_artifacts"],
                ]
            )
        if context and context.get("input_loading_modes"):
            runtime_context.extend(
                [
                    "Declared input loading modes:",
                    context["input_loading_modes"],
                    "A packet is a validated exact Downstream Contract; full and full_fallback paths remain complete authoritative sources.",
                ]
            )
        if context and context.get("delta_packet"):
            runtime_context.extend(
                [
                    f"Correction delta packet ({context.get('delta_packet_path', 'inline')}):",
                    context["delta_packet"],
                    "This is a derived manifest, not a new source of truth. Read previous_output and re-verify prior findings or requested changes item by item before completing this step.",
                ]
            )
        if context and context.get("user_input"):
            runtime_context.extend(
                ["Current user input for this iteration:", context["user_input"]]
            )

        if runtime_context:
            lines.extend(["Runtime context:"])
            lines.extend(runtime_context)
            lines.append("")

        if checklist_file is not None:
            lines.append("Do NOT finish this step until ALL checklist items are marked as [x].")

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
        valid_intents: List[PhaseStatusCode],
    ) -> Tuple[Optional[PhaseStatusCode], Optional[str]]:
        status = StatusCodeParser.extract(response, valid_codes=valid_intents)
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

        transform_runtime_context = hook_kwargs.get("transform_runtime_context")
        if callable(transform_runtime_context):
            runtime_context = transform_runtime_context(runtime_context)

        response = ""
        status_code: Optional[PhaseStatusCode] = None
        goto_target: Optional[str] = None
        agent_invoked = False
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
            continuation = runtime_context.get("continuation_prompt")
            if continuation:
                prompt = f"{prompt}\n\n{continuation}"
            response = agent_executor(prompt)
            agent_invoked = True

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
            if not after.continue_pipeline:
                return GenericPhaseExecution(
                    response=response,
                    status_code=status_code,
                    goto_target=goto_target,
                    context_updates=runtime_context,
                    events=events,
                    artifact_ready=artifact_ready,
                    published=False,
                    agent_invoked=agent_invoked,
                )

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
            agent_invoked=agent_invoked,
        )

    # Hooks that run on every step regardless of playbook declaration.
    # The alignment gate is registered on every step, but it is opt-in: it only
    # fires when the step declares an `alignment:` block (omitting the block, or
    # `alignment: {enabled: false}`, disables it).
    DEFAULT_STAGE_HOOKS: Dict[str, tuple] = {"prepare_input": ("AlignmentCheckpointGate",)}

    def _run_hook_stage(
        self,
        stage: str,
        **kwargs: Any,
    ) -> HookResult:
        declared = kwargs["step_def"].get("hooks", {}).get(stage, [])
        defaults = [
            name for name in self.DEFAULT_STAGE_HOOKS.get(stage, ()) if name not in declared
        ]
        trusted_hooks: list[object] = []
        trusted_artifact = {
            "cafe-spec": "spec",
            "cafe-plan": "plan",
        }.get(str(kwargs.get("skill_name") or ""))
        if (
            stage == "after_execute"
            and trusted_artifact is not None
            and kwargs["step_def"].get("output_artifact") == trusted_artifact
            and self.skill_loader.get_skill_entry(str(kwargs["skill_name"])).source == "builtin"
            and isinstance(kwargs.get("blackboard_state"), BlackboardState)
            and isinstance(getattr(kwargs.get("phase"), "issue_dir", None), Path)
        ):
            trusted_hooks.append(self._CONFIRMED_ARTIFACT_SYNC_HOOK)
        hook_entries = [*trusted_hooks, *defaults, *declared]
        aggregate = HookResult()

        for hook_entry in hook_entries:
            result: HookResult
            if hook_entry is self._CONFIRMED_ARTIFACT_SYNC_HOOK:
                result = self._run_confirmed_artifact_sync_hook(
                    stage=stage,
                    step_def=kwargs["step_def"],
                    skill_name=str(kwargs.get("skill_name", "")),
                    context=kwargs.get("context"),
                    response=kwargs.get("response"),
                    hook_kwargs=kwargs,
                )
            elif isinstance(hook_entry, str):
                hook_cls = self.hook_registry.get(str(hook_entry))
                if hook_cls is None:
                    raise ValueError(f"Unknown hook '{hook_entry}' in stage '{stage}'")
                hook = hook_cls()
                result = hook.run(stage=stage, **kwargs)
            elif isinstance(hook_entry, dict):
                result = self._run_script_hook(
                    stage=stage,
                    declaration=hook_entry,
                    step_def=kwargs["step_def"],
                    skill_name=str(kwargs.get("skill_name", "")),
                    context=kwargs.get("context"),
                    response=kwargs.get("response"),
                    hook_kwargs=kwargs,
                )
            else:
                raise ValueError(
                    f"Unsupported hook entry type '{type(hook_entry).__name__}' in stage '{stage}'"
                )

            aggregate.context_updates.update(result.context_updates)
            stage_context = kwargs.get("context")
            if isinstance(stage_context, dict):
                stage_context.update(result.context_updates)
            aggregate.events.extend(result.events)
            aggregate.artifact_ready = aggregate.artifact_ready and result.artifact_ready
            aggregate.retry_requested = aggregate.retry_requested or result.retry_requested
            if result.override_status_code is not None:
                aggregate.override_status_code = result.override_status_code
            if not result.continue_pipeline:
                aggregate.continue_pipeline = False
                break

        return aggregate

    def _run_script_hook(
        self,
        *,
        stage: str,
        declaration: Dict[str, Any],
        step_def: Dict[str, Any],
        skill_name: str,
        context: Optional[Dict[str, str]],
        response: Optional[str],
        hook_kwargs: Dict[str, Any],
    ) -> HookResult:
        if "capability" in declaration:
            raise ValueError("Capability hooks are runtime-owned")
        if stage not in self.SCRIPT_HOOK_STAGES:
            raise ValueError(
                f"Script hooks are only supported in {sorted(self.SCRIPT_HOOK_STAGES)}"
            )

        script, args_template, schema, when_intents, timeout_seconds = (
            self._parse_script_hook_declaration(declaration)
        )

        if when_intents:
            detected_status = self._detect_status_code(
                response=response or "",
                step_def=step_def,
                context=context,
                step_name=hook_kwargs.get("step_name"),
            )
            if detected_status not in when_intents:
                return HookResult(
                    events=[
                        {
                            "type": "script_hook",
                            "step": str(hook_kwargs.get("step_name") or ""),
                            "skill": skill_name,
                            "stage": stage,
                            "script": script,
                            "status": "skipped",
                            "reason": "intent_mismatch",
                            "detected_status": detected_status,
                            "when_intents": when_intents,
                        }
                    ]
                )

        with global_catalog_lock(self.skill_loader.global_root):
            script_path = self._resolve_script_path(skill_name=skill_name, script=script)
            resolved_args = self._resolve_script_args(
                args_template=args_template,
                context=context or {},
                hook_kwargs=hook_kwargs,
            )

            validation_errors: list[str] = []
            if schema is not None:
                validation_errors = validate_script_args_schema(args=resolved_args, schema=schema)
                if validation_errors:
                    return HookResult(
                        continue_pipeline=False,
                        override_status_code=PhaseStatusCode.NEED_PERMISSION,
                        events=[
                            {
                                "type": "script_hook",
                                "step": str(hook_kwargs.get("step_name") or ""),
                                "skill": skill_name,
                                "stage": stage,
                                "script": script,
                                "status": "validation_failed",
                                "exit_code": None,
                                "stdout": "",
                                "stderr": "",
                                "validation_errors": validation_errors,
                            }
                        ],
                    )

            cwd = Path.cwd().resolve()

            def request_for(candidate: Path) -> ScriptLaunchRequest:
                command = self._build_script_command(
                    script_path=candidate, args=resolved_args
                )
                return ScriptLaunchRequest(
                    execution_class=ExecutionClass.SANDBOX,
                    trust_source=TrustSource.WORKFLOW,
                    script=candidate,
                    args=tuple(command[2:]),
                    boundary=EffectiveBoundary(
                        cwd=cwd,
                        readable_roots=(cwd, candidate.parent),
                        writable_roots=(cwd,),
                        network_destinations=(),
                        environment=os.environ,
                    ),
                    timeout_seconds=timeout_seconds or 60.0,
                )

            try:
                skill_catalog_root = self.skill_loader.get_skill_dir(skill_name).parent
                script_snapshot = snapshot_script_tree(script_path, allowed_root=skill_catalog_root)
            except (OSError, ValueError):
                script_snapshot = None
                result = SandboxExecutor().run(request_for(script_path))

        if script_snapshot is not None:
            try:
                result = SandboxExecutor().run(
                    request_for(script_snapshot.path),
                    prepared_snapshot=script_snapshot,
                )
            finally:
                script_snapshot.cleanup()
        receipt = result.receipt.model_dump(mode="json")
        event = {
            "type": "script_hook",
            "step": str(hook_kwargs.get("step_name") or ""),
            "skill": skill_name,
            "stage": stage,
            "script": script,
            "status": result.receipt.outcome,
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "validation_errors": [],
            "execution_class": result.receipt.execution_class.value,
            "trust_source": result.receipt.trust_source.value,
            "canonical_identity": result.receipt.canonical_identity,
            "correlation_id": result.receipt.correlation_id,
            "effective_boundary": receipt["boundary"],
            "receipt": receipt,
        }
        if result.receipt.outcome != "success":
            event["migration"] = MIGRATION_GUIDANCE
            return HookResult(
                continue_pipeline=False,
                override_status_code=PhaseStatusCode.NEED_PERMISSION,
                events=[event],
            )
        return HookResult(events=[event])

    def _run_confirmed_artifact_sync_hook(
        self,
        *,
        stage: str,
        step_def: Dict[str, Any],
        skill_name: str,
        context: Optional[Dict[str, str]],
        response: Optional[str],
        hook_kwargs: Dict[str, Any],
    ) -> HookResult:
        if stage != "after_execute":
            raise ValueError("Confirmed artifact sync is restricted to after_execute")
        phase_name = "spec" if skill_name == "cafe-spec" else "plan"
        detected = self._detect_status_code(
            response=response or "",
            step_def=step_def,
            context=context,
            step_name=hook_kwargs.get("step_name"),
        )
        if detected != PhaseStatusCode.CONFIRMED.value:
            return HookResult(
                events=[
                    {
                        "type": "capability_hook",
                        "capability": CAPABILITY_ISSUE_COMMENT_ID,
                        "status": "skipped",
                        "reason": "intent_mismatch",
                    }
                ]
            )

        phase = hook_kwargs.get("phase")
        issue_dir = getattr(phase, "issue_dir", None)
        output_file = hook_kwargs.get("output_file")
        if not isinstance(issue_dir, Path) or not isinstance(output_file, Path):
            return HookResult(
                continue_pipeline=False,
                override_status_code=PhaseStatusCode.NEED_PERMISSION,
                events=[
                    {
                        "type": "capability_hook",
                        "capability": CAPABILITY_ISSUE_COMMENT_ID,
                        "status": "validation_failed",
                        "reason": "trusted_context_missing",
                    }
                ],
            )
        try:
            issue_config = (
                yaml.safe_load((issue_dir / "issue.yaml").read_text(encoding="utf-8")) or {}
            )
            phase_config = issue_config.get(phase_name) or {}
            issue_id = str((issue_config.get("spec") or {}).get("issue_id") or "").strip()
        except (OSError, yaml.YAMLError, AttributeError):
            return HookResult(
                continue_pipeline=False,
                override_status_code=PhaseStatusCode.NEED_PERMISSION,
                events=[
                    {
                        "type": "capability_hook",
                        "capability": CAPABILITY_ISSUE_COMMENT_ID,
                        "status": "validation_failed",
                        "reason": "issue_context_invalid",
                    }
                ],
            )
        if not phase_config.get("sync_github"):
            return HookResult(
                events=[
                    {
                        "type": "capability_hook",
                        "capability": CAPABILITY_ISSUE_COMMENT_ID,
                        "status": "skipped",
                        "reason": "sync_disabled",
                    }
                ]
            )
        if not issue_id or not output_file.is_file():
            return HookResult(
                continue_pipeline=False,
                override_status_code=PhaseStatusCode.NEED_PERMISSION,
                events=[
                    {
                        "type": "capability_hook",
                        "capability": CAPABILITY_ISSUE_COMMENT_ID,
                        "status": "validation_failed",
                        "reason": "confirmed_artifact_context_invalid",
                    }
                ],
            )

        repo_root = Path.cwd().resolve()
        try:
            output_arg = str(output_file.resolve().relative_to(repo_root))
        except ValueError:
            output_arg = str(output_file.resolve())
        artifact_sha256 = hashlib.sha256(output_file.read_bytes()).hexdigest()
        issue_write = f"github_issue_comment:{issue_id}"
        registry = load_capability_registry(default_capability_definition_dirs(repo_root))
        manifest = registry[CAPABILITY_ISSUE_COMMENT_ID]
        args = {
            "phase": phase_name,
            "output": output_arg,
            "issue_id": issue_id,
            "artifact_sha256": artifact_sha256,
        }
        request = {
            "capability": CAPABILITY_ISSUE_COMMENT_ID,
            "args": args,
            "effects": {
                "writes": [issue_write],
                "network_destinations": list(manifest.effects.network_destinations),
                "browser_open": [],
            },
            "credentials": list(manifest.credentials),
            "permissions": {
                "network": list(manifest.permissions["network"]),
                "writes": [issue_write],
            },
        }
        run = run_capability_request(
            repo_root=repo_root,
            registry=registry,
            capability_request=request,
            output_file=output_file,
        )
        blackboard_state = hook_kwargs.get("blackboard_state")
        if isinstance(blackboard_state, BlackboardState):
            BlackboardStore(issue_dir).append_capability_receipt(blackboard_state, run.receipt)
        event = {
            "type": "capability_hook",
            "capability": CAPABILITY_ISSUE_COMMENT_ID,
            "status": "success" if run.receipt.get("success") else "denied",
            "correlation_id": run.receipt.get("correlation_id"),
        }
        return HookResult(
            continue_pipeline=bool(run.receipt.get("success")),
            override_status_code=None
            if run.receipt.get("success")
            else PhaseStatusCode.NEED_PERMISSION,
            events=[event],
        )

    @staticmethod
    def _parse_script_hook_declaration(
        declaration: Dict[str, Any],
    ) -> tuple[str, Dict[str, Any], Optional[Dict[str, Any]], list[str], Optional[float]]:
        allowed_fields = {
            "script",
            "args",
            "schema",
            "when_intents",
            "timeout_seconds",
            "execution_class",
        }
        unknown = sorted(set(declaration.keys()) - allowed_fields)
        if unknown:
            raise ValueError(f"Script hook contains unsupported fields: {unknown}")

        execution_class = declaration.get("execution_class", ExecutionClass.SANDBOX.value)
        if execution_class != ExecutionClass.SANDBOX.value:
            raise ValueError("Script hooks may only declare sandbox execution")

        script = declaration.get("script")
        if not isinstance(script, str) or not script.strip():
            raise ValueError("Script hook requires non-empty 'script'")
        script = script.strip()

        args = declaration.get("args", {})
        if args is None:
            args = {}
        if not isinstance(args, dict):
            raise ValueError("Script hook 'args' must be an object")

        schema = declaration.get("schema")
        if schema is not None and not isinstance(schema, dict):
            raise ValueError("Script hook 'schema' must be an object")

        when_intents_raw = declaration.get("when_intents", [])
        if when_intents_raw is None:
            when_intents_raw = []
        if not isinstance(when_intents_raw, list) or not all(
            isinstance(item, str) for item in when_intents_raw
        ):
            raise ValueError("Script hook 'when_intents' must be a list of strings")

        timeout_seconds_raw = declaration.get("timeout_seconds")
        timeout_seconds: Optional[float] = None
        if timeout_seconds_raw is not None:
            if isinstance(timeout_seconds_raw, bool) or not isinstance(
                timeout_seconds_raw, (int, float)
            ):
                raise ValueError("Script hook 'timeout_seconds' must be a positive number")
            if timeout_seconds_raw <= 0:
                raise ValueError("Script hook 'timeout_seconds' must be a positive number")
            timeout_seconds = float(timeout_seconds_raw)

        return (
            script,
            args,
            schema,
            [item.strip() for item in when_intents_raw if item.strip()],
            timeout_seconds,
        )

    def _resolve_script_path(self, *, skill_name: str, script: str) -> Path:
        script_path = Path(script)
        if script_path.is_absolute():
            raise ValueError("Script hook does not allow absolute paths")

        script_parts = list(script_path.parts)
        if any(part == ".." for part in script_parts):
            raise ValueError("Script hook does not allow parent traversal")

        if script_parts and script_parts[0] == "scripts":
            script_parts = script_parts[1:]
        if not script_parts:
            raise ValueError("Script hook script path is empty")

        skill_dir = self.skill_loader.get_skill_dir(skill_name)
        scripts_dir = (skill_dir / "scripts").resolve()
        candidate = (scripts_dir / Path(*script_parts)).absolute()

        try:
            candidate.relative_to(scripts_dir)
        except ValueError:
            raise ValueError("Script hook path must stay inside skill scripts directory")
        if not candidate.exists() or not candidate.is_file():
            raise ValueError(f"Script hook file not found: {candidate}")
        return candidate

    def _resolve_script_args(
        self,
        *,
        args_template: Dict[str, Any],
        context: Dict[str, str],
        hook_kwargs: Dict[str, Any],
    ) -> Dict[str, Any]:
        template_values: Dict[str, str] = {}
        for key, value in context.items():
            if value is None:
                continue
            template_values[key] = str(value)

        for key, value in hook_kwargs.items():
            if value is None:
                continue
            if isinstance(value, (str, int, float, bool, Path)):
                template_values[key] = str(value)

        resolved: Dict[str, Any] = {}
        for key, value in args_template.items():
            resolved[str(key)] = self._resolve_script_arg_value(
                value=value, template_values=template_values
            )
        return resolved

    def _resolve_script_arg_value(
        self,
        *,
        value: Any,
        template_values: Dict[str, str],
    ) -> Any:
        if isinstance(value, str):
            try:
                return value.format(**template_values)
            except KeyError as exc:
                raise ValueError(
                    f"Missing template value for '{exc.args[0]}' in script hook args"
                ) from exc
        if isinstance(value, list):
            return [
                self._resolve_script_arg_value(value=item, template_values=template_values)
                for item in value
            ]
        if isinstance(value, dict):
            return {
                str(key): self._resolve_script_arg_value(
                    value=item, template_values=template_values
                )
                for key, item in value.items()
            }
        return value

    @staticmethod
    def _build_script_command(*, script_path: Path, args: Dict[str, Any]) -> list[str]:
        cmd = ["/bin/bash", str(script_path)]
        for key, value in args.items():
            flag = f"--{key}"
            if isinstance(value, list):
                for item in value:
                    cmd.extend([flag, str(item)])
                continue
            if isinstance(value, bool):
                cmd.extend([flag, "true" if value else "false"])
                continue
            cmd.extend([flag, str(value)])
        return cmd

    @staticmethod
    def _read_baton_intent(
        *, next_step_path: Optional[str], step_name: Optional[str]
    ) -> Optional[str]:
        if not next_step_path:
            return None

        try:
            raw = Path(next_step_path).read_text(encoding="utf-8").strip()
        except OSError:
            return None

        if not raw:
            return None

        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None

        if not isinstance(payload, dict):
            return None

        if payload.get("source") == "workflow.start_step_override" and (
            not step_name
            or str(payload.get("to_step", payload.get("from_step", ""))) == str(step_name)
        ):
            return None

        if (
            step_name
            and payload.get("from_step")
            and str(payload.get("from_step", "")) != str(step_name)
        ):
            return None

        intent = payload.get("intent")
        if not isinstance(intent, str):
            return None
        return intent.strip() or None

    @staticmethod
    def _detect_status_code(
        *,
        response: str,
        step_def: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
        step_name: Optional[str] = None,
    ) -> Optional[str]:
        valid = effective_step_status_codes(step_def)

        context = context or {}
        valid_values = {status.value for status in valid}

        next_step_intent = GenericPhase._read_baton_intent(
            next_step_path=context.get("next_step_path"),
            step_name=step_name,
        )
        if next_step_intent in valid_values:
            return next_step_intent

        status_code = StatusCodeParser.extract(response, valid_codes=valid)
        return status_code.value if status_code is not None else None
