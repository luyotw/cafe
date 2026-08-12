"""Direct playbook step execution through GenericPhase."""

from __future__ import annotations

import errno
import json
import os
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

from cafe.agents.manager import AgentManager
from cafe.core.blackboard import (
    ArtifactEntry,
    ArtifactKind,
    BlackboardState,
    BlackboardStore,
    HandoffContract,
    HandoffIntent,
    HandoffOwner,
    LongRunningOperationArtifact,
    operation_artifact_path,
    operation_receipt_path,
)
from cafe.core.capabilities import CAPABILITY_PR_PUBLISH_ID
from cafe.core.context_packet import (
    build_context_packet_diagnostics,
    format_context_packet_diagnostic,
)
from cafe.core.delta_packet import (
    build_delta_packet,
    inline_delta_packet,
    persist_delta_input_snapshot,
    persist_delta_packet,
)
from cafe.core.git import GitOperations
from cafe.core.long_running_operation_helper import get_operation_status
from cafe.core.phase import Phase
from cafe.core.playbook import resolve_playbook_skills, resolve_step_behavior
from cafe.core.resume_user_input import (
    is_interrupted_iteration,
    load_prior_run_context,
    prior_cli_and_session,
    resolve_resume_user_input,
)
from cafe.core.session_continuation import (
    SessionContinuation,
    exact_continuation_from_context,
)
from cafe.core.status_codes import (
    PhaseStatusCode,
    StatusCodeParser,
    effective_step_handoff_intents,
    effective_step_status_codes,
    step_on_declares,
    transition_map_key,
)
from cafe.core.takeover import build_takeover_snapshot
from cafe.core.types import AgentCLI
from cafe.core.workflow_models import StepExecutionResult
from cafe.core.workflow_runtime import (
    operation_artifact_is_trusted,
    operation_receipt_is_trusted,
)
from cafe.phases.generic_phase import GenericPhase
from cafe.skills.checklist_composer import compose_declared_checklist
from cafe.skills.contracts import (
    DeclaredArtifactError,
    SkillWorkflowContract,
    resolve_effective_prompt_inputs,
    resolve_packet_requested_placeholders,
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
    """Realign stale feedback batons using the declared repair target."""
    behavior = resolve_step_behavior(playbook, step_name)
    if not status_code or behavior.feedback_target is None:
        return
    if status_code != PhaseStatusCode.NEEDS_CHANGES.value:
        return

    allowed = list(playbook.get("steps", {}).keys())
    store = BlackboardStore(issue_dir)
    contract = store.load_handoff_contract(
        blackboard_state,
        allowed_steps=allowed,
    )
    if contract.to_owner != HandoffOwner.AGENT or contract.to_step != step_name:
        return

    store.update_handoff_contract(
        blackboard_state,
        from_step=step_name,
        to_owner=HandoffOwner.AGENT,
        to_step=behavior.feedback_target,
        intent=HandoffIntent.AWAIT_AGENT,
        status_code=status_code,
        source="workflow.declared_feedback_target",
    )
    store.set_handoff_summary(
        blackboard_state,
        f"Feedback requires work; next declared step is {behavior.feedback_target}.",
    )


class GenericWorkflowStepExecutor(Phase):
    """Execute one playbook step without shelling out to legacy CLI commands."""

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
        # execute_step() replaces this with an explicit invocation-scoped
        # decision; AUTO only preserves legacy behavior for direct helper use.
        self._session_continuation = SessionContinuation.auto()
        self._delta_packet_metadata: Optional[Dict[str, Any]] = None
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

    @staticmethod
    def _read_regular_file(path: Path, *, fail_closed: bool) -> Optional[bytes]:
        """Read a control file without following a replacement symlink."""
        try:
            mode = os.lstat(path).st_mode
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(mode):
            if fail_closed:
                raise RuntimeError(f"canonical control file must be regular: {path}")
            return None

        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except OSError as exc:
            if not fail_closed and exc.errno in {errno.ENOENT, errno.ELOOP}:
                return None
            raise
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                if fail_closed:
                    raise RuntimeError(f"canonical control file must be regular: {path}")
                return None
            return handle.read()

    @staticmethod
    def _remove_control_path(path: Path) -> None:
        """Remove a control-path entry without following a symlink."""
        try:
            mode = os.lstat(path).st_mode
        except FileNotFoundError:
            return
        if stat.S_ISDIR(mode):
            shutil.rmtree(path)
        else:
            path.unlink()

    @classmethod
    def _restore_control_file(cls, path: Path, content: Optional[bytes]) -> None:
        """Restore one control file by replacing its directory entry atomically."""
        if content is None:
            cls._remove_control_path(path)
            return

        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_path = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
            cls._remove_control_path(path)
            os.replace(temporary_path, path)
        except BaseException:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass
            raise

    def _preserve_runtime_operation_metadata(
        self,
        *,
        snapshot: Optional[bytes],
        observed: Optional[bytes],
        step_name: str,
        iteration_dir: Path,
    ) -> Optional[bytes]:
        """Merge verified operation metadata published while a hybrid call ran."""
        if snapshot is None or observed is None:
            return snapshot
        try:
            original_state = BlackboardState.from_dict(json.loads(snapshot), initial_step=step_name)
            observed_state = BlackboardState.from_dict(json.loads(observed), initial_step=step_name)
            operation_bytes = self._read_regular_file(
                operation_artifact_path(iteration_dir), fail_closed=False
            )
            if operation_bytes is None:
                return snapshot
            operation = LongRunningOperationArtifact.from_dict(json.loads(operation_bytes))
        except (OSError, ValueError, json.JSONDecodeError):
            return snapshot

        store = BlackboardStore(self.issue_dir)
        if not operation_artifact_is_trusted(
            blackboard_store=store,
            blackboard=observed_state,
            current_step=step_name,
            iteration_dir=iteration_dir,
            artifact=operation,
        ):
            return snapshot

        operation_name = f"{step_name}_operation"
        original_state.artifacts[operation_name] = observed_state.artifacts[operation_name]
        try:
            receipt_bytes = self._read_regular_file(
                operation_receipt_path(iteration_dir), fail_closed=False
            )
            if receipt_bytes is not None:
                receipt = LongRunningOperationArtifact.from_dict(json.loads(receipt_bytes))
                if operation_receipt_is_trusted(
                    blackboard_store=store,
                    blackboard=observed_state,
                    current_step=step_name,
                    iteration_dir=iteration_dir,
                    operation=operation,
                    receipt=receipt,
                ):
                    receipt_name = f"{step_name}_operation_receipt"
                    original_state.artifacts[receipt_name] = observed_state.artifacts[receipt_name]
        except (OSError, ValueError, json.JSONDecodeError):
            pass
        return json.dumps(original_state.to_dict(), ensure_ascii=False, indent=2).encode("utf-8")

    def _preserve_hybrid_control_files(
        self,
        action: Callable[[], Any],
        *,
        step_name: str,
        iteration_dir: Path,
    ) -> Any:
        """Run an agent call without allowing it to persist canonical workflow control.

        Hybrid portions may retain ordinary source-editing capabilities.  Those
        capabilities cannot also become authority to route the outer workflow:
        a portion writes only its private baton, while the runtime restores the
        canonical baton and blackboard even when the agent call is interrupted.
        """
        control_files = (self.issue_dir / "blackboard.json", self.issue_dir / "next_step.txt")
        snapshots = {
            path: self._read_regular_file(path, fail_closed=True) for path in control_files
        }
        try:
            return action()
        finally:
            blackboard_path = self.issue_dir / "blackboard.json"
            restored = dict(snapshots)
            restored[blackboard_path] = self._preserve_runtime_operation_metadata(
                snapshot=snapshots[blackboard_path],
                observed=self._read_regular_file(blackboard_path, fail_closed=False),
                step_name=step_name,
                iteration_dir=iteration_dir,
            )
            for path, content in restored.items():
                self._restore_control_file(path, content)

    def execute(self) -> Any:
        raise NotImplementedError("GenericWorkflowStepExecutor only supports execute_step()")

    def execute_step(
        self,
        step_name: str,
        step_def: Dict[str, Any],
        blackboard_state: BlackboardState,
        extra_prompt: Optional[str] = None,
        same_invocation_retry: bool = False,
    ) -> StepExecutionResult:
        hybrid_portion = step_def.get("hybrid_portion")
        is_hybrid_portion = isinstance(hybrid_portion, Mapping)
        baton_path = self.issue_dir / "next_step.txt"
        self.phase_name = step_name
        self.phase_dir = self.issue_dir / step_name
        self.phase_dir.mkdir(parents=True, exist_ok=True)

        self.iteration = self._get_next_iteration_number(step_name, self.phase_dir)
        self._resolved_iteration_user_input = None
        self._delta_packet_metadata = None
        iteration_dir = self._get_iteration_dir(self.iteration)
        iteration_dir.mkdir(parents=True, exist_ok=True)
        portion_baton_path = (
            iteration_dir / "hybrid_portion_baton.json" if is_hybrid_portion else None
        )
        if portion_baton_path is not None:
            # A hybrid portion receives a private completion sink.  The
            # canonical baton remains untouched even if the agent is stopped.
            portion_baton_path.write_text("", encoding="utf-8")

        output_file = self._get_versioned_file_path(step_name, self.iteration, self.phase_dir)
        checklist_file = iteration_dir / "checklist.md"
        questions_xml_file = iteration_dir / "questions.xml"
        capability_request_file = iteration_dir / "capability_request.json"
        publish_request_file = iteration_dir / "publish_request.json"
        self._current_output_file = output_file

        # A reused iteration is an interrupted run. Preserve any partial output
        # it already produced; only seed a genuinely new correction iteration.
        if self.iteration > 1 and not output_file.exists():
            self._copy_previous_version(step_name, self.iteration, self.phase_dir)
        self._ensure_output_file_initialized(step_name, output_file)
        behavior = resolve_step_behavior(self.playbook, step_name)
        capability_ids = self._effective_capability_ids(step_name, step_def)
        if capability_ids:
            self._write_capability_request(
                output_file=output_file,
                capability_request_file=capability_request_file,
                capability_ids=capability_ids,
            )
            if behavior.publish_confirmation:
                self._write_publish_request(
                    output_file=output_file,
                    publish_request_file=publish_request_file,
                )

        skill_name = self._resolve_skill_name(step_def, self.iteration)
        valid_intents = self._resolve_valid_intents(step_def)
        agent_name = self._resolve_agent_name(step_name, step_def)
        self._step_agent_name = agent_name
        self._session_continuation = self._select_session_continuation(
            agent_name=agent_name,
            step_def=step_def,
            same_invocation_retry=same_invocation_retry,
        )
        self._apply_step_agent_model(step_name=step_name, step_def=step_def, agent_name=agent_name)
        agent_cli = self.agent_manager.get_agent(agent_name).config.cli
        context = self._build_context(
            step_name=step_name,
            step_def=step_def,
            blackboard_state=blackboard_state,
            agent_name=agent_name,
            output_file=output_file,
            baton_path=portion_baton_path or baton_path,
        )
        contract = self._get_skill_loader().get_workflow_contract(skill_name)
        self._template_allowed_directories = self._template_allowed_directories_for(
            step_name=step_name,
            step_def=step_def,
            skill_name=skill_name,
            contract=contract,
        )
        workflow_skill_names = resolve_playbook_skills(
            self.playbook,
            channel="workflow",
            role=step_def.get("role"),
            step_name=step_name,
        )
        self.generic_phase.skill_bridge.synchronize_skills(
            [*workflow_skill_names, skill_name],
            agent_cli,
            install=False,
        )
        shared_skill_invocations = self.generic_phase.prepare_skills(
            skill_names=workflow_skill_names,
            agent_cli=agent_cli,
            context=context,
        )
        skill_invocation = self.generic_phase.prepare_skill(
            skill_name=skill_name,
            agent_cli=agent_cli,
            context=context,
        )
        if not checklist_file.exists():
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
            baton_path=portion_baton_path,
        )
        phase_specific_data = {
            "step_name": step_name,
            "skill_name": skill_name,
            "playbook_id": self.playbook.get("playbook", {}).get("id"),
        }
        require_status_code = self._step_requires_status_code(step_name)

        def run_agent(prompt: str) -> str:
            last_prompt[:] = [prompt]
            if self._delta_packet_metadata is not None:
                phase_specific_data["delta_packet"] = dict(self._delta_packet_metadata)
            resolved_user_input = self._get_resolved_iteration_user_input(step_name)
            attempt_allowed_tools = (
                self._build_baton_retry_allowed_tools()
                if self._is_baton_retry_user_input(resolved_user_input)
                else allowed_tools
            )
            try:

                def execute_agent() -> tuple[str, Optional[PhaseStatusCode]]:
                    return self._execute_agent_iteration(
                        agent_name=agent_name,
                        prompt=prompt,
                        user_input=resolved_user_input,
                        valid_intents=valid_intents,
                        require_status_code=False,
                        persist_status=False,
                        allowed_tools=attempt_allowed_tools,
                        phase_specific_data=phase_specific_data,
                        backup_context_callback=lambda error: self._build_backup_takeover_context(
                            error=error,
                            step_name=step_name,
                            step_def=step_def,
                            blackboard_state=blackboard_state,
                            output_file=output_file,
                            checklist_file=checklist_file,
                            iteration_dir=iteration_dir,
                        ),
                    )

                if is_hybrid_portion:
                    response, _ = self._preserve_hybrid_control_files(
                        execute_agent,
                        step_name=step_name,
                        iteration_dir=iteration_dir,
                    )
                else:
                    response, _ = execute_agent()
            finally:
                # A phase agent can launch a controlled long-running operation,
                # whose helper publishes runtime-owned metadata while this
                # executor still holds the blackboard snapshot from before the
                # agent call. Refresh that shared object before after-execute
                # hooks or artifact writes can persist the stale snapshot and
                # erase the operation's trust record.
                refreshed = BlackboardStore(self.issue_dir).load_or_create(
                    step_name,
                    playbook_id=str(self.playbook.get("playbook", {}).get("id", "default")),
                    tolerate_invalid_baton=True,
                )
                blackboard_state.__dict__.update(refreshed.__dict__)
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
                "authoritative_inputs": context.get("authoritative_inputs", {}),
                "capability_request_file": capability_request_file if capability_ids else None,
                "publish_request_file": (
                    publish_request_file
                    if resolve_step_behavior(self.playbook, step_name).publish_confirmation
                    else None
                ),
                "blackboard_state": blackboard_state,
                "transform_runtime_context": (
                    lambda runtime_context: self._apply_resume_to_runtime_context(
                        runtime_context,
                        step_name,
                        blackboard_state,
                        extra_prompt,
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
            validate_completion = lambda: self._validate_and_retry_checklist_completion(
                agent_name=agent_name,
                prompt=last_prompt[0] if last_prompt else "",
                user_input=resolved_user_input,
                valid_intents=valid_intents,
                allowed_tools=allowed_tools,
                max_retries=3,
            )
            response, validated_status, validation_passed = (
                self._preserve_hybrid_control_files(
                    validate_completion,
                    step_name=step_name,
                    iteration_dir=iteration_dir,
                )
                if is_hybrid_portion
                else validate_completion()
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

        captured_hybrid_baton: Optional[str] = None
        if portion_baton_path is not None and portion_baton_path.exists():
            try:
                captured = portion_baton_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                captured = "<non-utf8 baton>"
            if captured:
                captured_hybrid_baton = captured

        if status_code is not None and not is_hybrid_portion:
            # If the agent already wrote a valid baton (next_step.txt),
            # skip the status-code-driven baton write so we don't overwrite
            # the agent's explicit handoff.  This is the baton-first path.
            if not self._agent_wrote_baton(step_name, step_def):
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

        if captured_hybrid_baton is not None:
            events.append(
                {
                    "type": "hybrid_portion_baton",
                    "portion": dict(hybrid_portion),
                    "payload": captured_hybrid_baton,
                }
            )

        return StepExecutionResult(
            response=response,
            artifacts=artifacts,
            status_code=effective_status.value if effective_status is not None else None,
            auto_continue=auto_continue,
            events=events,
        )

    def _build_backup_takeover_context(
        self,
        *,
        error: object,
        step_name: str,
        step_def: Dict[str, Any],
        blackboard_state: BlackboardState,
        output_file: Path,
        checklist_file: Path,
        iteration_dir: Path,
    ) -> str:
        """Refresh a bounded cold-takeover snapshot just before a backup runs."""
        contract = self._get_skill_loader().get_workflow_contract(
            self._resolve_skill_name(step_def, self.iteration)
        )
        input_artifacts = self._step_input_artifacts(step_def, blackboard_state)
        authoritative_inputs = resolve_prompt_inputs(contract, input_artifacts)
        packet_requested_placeholders = self._packet_requested_placeholders(
            contract,
            input_artifacts,
            step=step_name,
            iteration=self.iteration,
            feedback=bool(
                input_artifacts.get("review_feedback") or input_artifacts.get("pr_result")
            ),
            authoritative_inputs=authoritative_inputs,
        )
        resolved_inputs = self._load_persisted_effective_inputs(
            iteration_dir,
            require_persisted_packet_decision=bool(packet_requested_placeholders),
            authoritative_inputs=authoritative_inputs,
            packet_requested_placeholders=packet_requested_placeholders,
            target_step=step_name,
            iteration=self.iteration,
        )
        if resolved_inputs is None:
            if packet_requested_placeholders:
                raise ValueError("Missing pre-launch context packet decision for backup takeover")
            resolved_inputs = resolve_effective_prompt_inputs(
                contract,
                input_artifacts,
                step=step_name,
                iteration=self.iteration,
                feedback=bool(
                    input_artifacts.get("review_feedback") or input_artifacts.get("pr_result")
                ),
                packet_dir=iteration_dir,
            )
        workspace: dict[str, Any] = {}
        try:
            workspace["head"] = self.git_ops.run_git("rev-parse", "HEAD")
            workspace["changed"] = [
                line[3:] for line in self.git_ops.get_status().splitlines()[:100] if len(line) >= 4
            ]
        except Exception:
            workspace["state"] = "unknown"

        operation: dict[str, Any] | None = None
        operation_store = BlackboardStore(self.issue_dir)
        try:
            stored_operation = operation_store.read_operation_artifact(iteration_dir)
            if stored_operation is not None:
                current_blackboard = operation_store.load_or_create(step_name)
                if not operation_artifact_is_trusted(
                    blackboard_store=operation_store,
                    blackboard=current_blackboard,
                    current_step=step_name,
                    iteration_dir=iteration_dir,
                    artifact=stored_operation,
                ):
                    operation = {"state": "unknown"}
                else:
                    receipt = operation_store.read_operation_receipt(iteration_dir)
                    if receipt is not None and not operation_receipt_is_trusted(
                        blackboard_store=operation_store,
                        blackboard=current_blackboard,
                        current_step=step_name,
                        iteration_dir=iteration_dir,
                        operation=stored_operation,
                        receipt=receipt,
                    ):
                        operation = {"state": "unknown"}
                    else:
                        current = get_operation_status(
                            issue_dir=self.issue_dir,
                            step=step_name,
                            iteration_dir=iteration_dir,
                            playbook=self.playbook,
                        )
                        operation = {
                            "state": "running" if current.state.value == "running" else "terminal",
                            "id": current.operation_id,
                        }
        except (OSError, ValueError, json.JSONDecodeError):
            # Unknown operation evidence is unsafe to treat as absent: a cold
            # backup must status-check rather than risk relaunching it.
            operation = {"state": "unknown"}
        snapshot = build_takeover_snapshot(
            reason=error,
            step=step_name,
            iteration=self.iteration,
            resolved_inputs=resolved_inputs,
            output_file=output_file,
            checklist_file=checklist_file,
            operation=operation,
            workspace=workspace,
        )
        return json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _load_persisted_effective_inputs(
        iteration_dir: Path,
        *,
        require_persisted_packet_decision: bool = False,
        authoritative_inputs: Mapping[str, str | Path] | None = None,
        packet_requested_placeholders: frozenset[str] | None = None,
        target_step: str | None = None,
        iteration: int | None = None,
    ) -> dict[str, dict[str, Any]] | None:
        """Reuse pre-launch packet decisions instead of resolving them during takeover."""
        path = iteration_dir / "iteration.json"
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid persisted context packet decision") from exc
        if not isinstance(raw, dict):
            raise ValueError("Invalid persisted context packet decision")
        if "effective_inputs" not in raw:
            if require_persisted_packet_decision:
                raise ValueError("Invalid persisted context packet decision")
            return None
        persisted = raw.get("effective_inputs")
        if isinstance(persisted, dict):
            from cafe.core.context_packet import validate_effective_input_bindings

            return validate_effective_input_bindings(
                persisted,
                authoritative_inputs=authoritative_inputs,
                packet_requested_placeholders=packet_requested_placeholders,
                packet_dir=iteration_dir,
                target_step=target_step,
                iteration=iteration,
            )
        raise ValueError("Invalid persisted context packet decision")

    @staticmethod
    def _packet_requested_placeholders(
        contract: SkillWorkflowContract,
        artifacts: Mapping[str, Any],
        *,
        step: str,
        iteration: int,
        feedback: bool,
        authoritative_inputs: Mapping[str, str | Path],
    ) -> frozenset[str]:
        """Resolve the active packet policies from the skill-owned contract."""
        return resolve_packet_requested_placeholders(
            contract,
            artifacts,
            step=step,
            iteration=iteration,
            feedback=feedback,
            authoritative_inputs=authoritative_inputs,
        )

    @staticmethod
    def _persist_context_packet_diagnostics(
        iteration_dir: Path, effective_inputs: Mapping[str, Mapping[str, Any]]
    ) -> None:
        """Persist the validated packet decision in the consumer's sole iteration record."""
        build_context_packet_diagnostics(effective_inputs)
        path = iteration_dir / "iteration.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("Unable to persist context packet diagnostics") from exc
        if not isinstance(raw, dict):
            raise ValueError("Invalid iteration metadata for context packet diagnostics")
        raw["effective_inputs"] = {key: dict(value) for key, value in effective_inputs.items()}
        # ``effective_inputs`` is the only persisted decision.  Discard the
        # obsolete projection so status cannot diverge from launch inputs.
        raw.pop("context_packets", None)
        path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

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

        step_def = self.playbook.get("steps", {}).get(step_name, {})
        if self.iteration == 1 and self._declared_human_task_id(step_def, "initial"):
            return ""
        return "workflow execute"

    def _apply_resume_user_input_to_candidate(self, step_name: str, candidate: str) -> str:
        agent_name = getattr(self, "_step_agent_name", None)
        if not agent_name or not hasattr(self, "agent_manager"):
            return candidate

        previous_data = self._load_previous_iteration_data()
        current_data = self._load_current_iteration_data()
        if not is_interrupted_iteration(
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
                continuation=self._current_session_continuation(),
            )
        except Exception:
            default_config = self.agent_manager.get_agent(agent_name).config
            execution_config = self._fail_closed_execution_config(
                default_config,
                self._current_session_continuation(),
            )

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
        blackboard_state: Optional[BlackboardState] = None,
        extra_prompt: Optional[str] = None,
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
        if extra_prompt:
            resolved = f"{extra_prompt}\n\n{resolved}" if resolved else extra_prompt
        self._resolved_iteration_user_input = resolved
        if resolved:
            updated["user_input"] = resolved
        else:
            updated.pop("user_input", None)

        # On an interrupted run, surface only the current step's declared
        # Blackboard artifact paths so the agent re-grounds on the current scope.
        previous_data = self._load_previous_iteration_data()
        current_data = self._load_current_iteration_data()
        step_def = self.playbook.get("steps", {}).get(step_name, {})
        declared_artifacts = step_def.get("input_artifacts") if isinstance(step_def, dict) else None
        if (
            blackboard_state is not None
            and isinstance(declared_artifacts, list)
            and is_interrupted_iteration(
                iteration=self.iteration,
                previous_iteration_data=previous_data,
                current_iteration_data=current_data,
            )
        ):
            artifact_lines = []
            for artifact_name in declared_artifacts:
                name = str(artifact_name)
                artifact = blackboard_state.artifacts.get(name)
                path = artifact.path if artifact is not None else None
                if isinstance(path, str) and path.strip():
                    artifact_lines.append(f"- {name}: {path}")
            if artifact_lines:
                updated["resume_input_artifacts"] = "\n".join(artifact_lines)

        if self.iteration > 1 and blackboard_state is not None:
            packet, metadata = self._prepare_delta_packet(
                step_name=step_name,
                blackboard_state=blackboard_state,
                user_input=resolved,
            )
            self._delta_packet_metadata = metadata
            updated["delta_packet"] = inline_delta_packet(packet, metadata)
            updated["delta_packet_path"] = str(metadata["path"])

        return updated

    def _configured_clis_for_agent(self, agent_name: str) -> list[AgentCLI]:
        try:
            config = self.agent_manager.get_agent(agent_name).config
        except Exception:
            return []
        if getattr(config, "clis", None):
            return [
                entry.cli
                for entry in config.clis
                if hasattr(entry, "cli") and isinstance(entry.cli, AgentCLI)
            ]
        configured = []
        if isinstance(getattr(config, "cli", None), AgentCLI):
            configured.append(config.cli)
        configured.extend(
            cli
            for cli in getattr(config, "backup_clis", [])
            if isinstance(cli, AgentCLI) and cli not in configured
        )
        return configured

    def _select_session_continuation(
        self,
        *,
        agent_name: str,
        step_def: Dict[str, Any],
        same_invocation_retry: bool = False,
    ) -> SessionContinuation:
        """Choose once per step invocation; retries update it after success."""
        previous_data = self._load_previous_iteration_data()
        current_data = self._load_current_iteration_data()
        configured_clis = self._configured_clis_for_agent(agent_name)

        if is_interrupted_iteration(
            iteration=self.iteration,
            previous_iteration_data=previous_data,
            current_iteration_data=current_data,
        ):
            exact = exact_continuation_from_context(
                current_data,
                configured_clis=configured_clis,
            )
            return exact or SessionContinuation.new()

        if same_invocation_retry:
            exact = exact_continuation_from_context(
                previous_data,
                configured_clis=configured_clis,
            )
            return exact or SessionContinuation.new()

        return SessionContinuation.new()

    def _git_snapshot(self) -> Dict[str, str]:
        snapshot: Dict[str, str] = {}
        run_git = getattr(self.git_ops, "run_git", None)
        if not callable(run_git):
            return snapshot
        try:
            snapshot["head_sha"] = run_git("rev-parse", "HEAD")
            base_branch = str(
                self._get_issue_config_value(
                    self.issue_dir / "issue.yaml",
                    "base_branch",
                )
                or self.git_ops.get_default_base_branch()
            )
            snapshot["base_ref"] = base_branch
            snapshot["base_sha"] = run_git("rev-parse", base_branch)
        except Exception:
            return {}
        return snapshot

    def _prepare_delta_packet(
        self,
        *,
        step_name: str,
        blackboard_state: BlackboardState,
        user_input: str,
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        step_def = self.playbook.get("steps", {}).get(step_name, {})
        declared_artifacts = self._step_input_artifacts(step_def, blackboard_state)
        iteration_dir = self._get_iteration_dir(self.iteration)
        packet_path = iteration_dir / "delta_packet.json"
        delta_input_path = iteration_dir / "delta_input.md"
        delta_input = persist_delta_input_snapshot(delta_input_path, user_input)
        previous_output = self._get_versioned_file_path(
            step_name,
            self.iteration - 1,
            self.phase_dir,
        )
        packet = build_delta_packet(
            issue_name=self.issue_name,
            step_name=step_name,
            iteration=self.iteration,
            blackboard_state=blackboard_state,
            declared_artifacts=declared_artifacts,
            previous_output=previous_output,
            user_input_path=delta_input_path,
            user_input=delta_input,
            git_snapshot=self._git_snapshot(),
        )
        current_data = self._load_current_iteration_data()
        persisted_metadata = (
            current_data.get("delta_packet") if isinstance(current_data, dict) else None
        )
        expected_sha256 = (
            persisted_metadata.get("sha256")
            if isinstance(persisted_metadata, dict)
            and isinstance(persisted_metadata.get("sha256"), str)
            else None
        )
        return persist_delta_packet(
            packet_path,
            packet,
            expected_sha256=expected_sha256,
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
            raise ValueError(f"invalid phase config for '{step_name}' in '{step_name}': {exc}")

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
        return effective_step_status_codes(step_def)

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
        baton_path: Optional[Path] = None,
    ) -> List[str]:
        allowed_tools = self._normalize_allowed_tools(step_def.get("allowed_tools", []))

        def add(tool: Optional[str]) -> None:
            if tool and tool not in allowed_tools:
                allowed_tools.append(tool)

        def add_writable_file(path: Path) -> None:
            display_path = self._display_path(path)
            # Claude uses Write for file creation and full replacement, and
            # Edit for in-place changes.  Runtime artifacts must support both.
            add(f"edit({display_path})")
            add(f"write({display_path})")

        add("ls")

        if baton_path is None:
            # Always allow writing blackboard and baton so agents can hand off
            # without hitting permission denials (runtime also writes these,
            # but belt-and-suspenders prevents the agent from wasting tokens
            # asking for permission).
            add_writable_file(self.issue_dir / "blackboard.json")
            add_writable_file(self.issue_dir / "next_step.txt")
        else:
            add_writable_file(baton_path)

        add_writable_file(output_file)
        add_writable_file(checklist_file)

        if step_on_declares(step_def, "need_clarification"):
            add_writable_file(questions_xml_file)

        grants = resolve_step_behavior(self.playbook, step_name).runtime_tool_grants
        if "web_research" in grants:
            add("web_fetch")
            add("web_search")
        if "git_inspection" in grants:
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
        for path in (self.issue_dir / "blackboard.json", self.issue_dir / "next_step.txt"):
            display_path = self._display_path(path)
            allowed_tools.append(f"edit({display_path})")
            allowed_tools.append(f"write({display_path})")
        return allowed_tools

    def _build_context(
        self,
        *,
        step_name: str,
        step_def: Dict[str, Any],
        blackboard_state: BlackboardState,
        agent_name: str,
        output_file: Path,
        baton_path: Optional[Path] = None,
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
        valid_baton_intents = effective_step_handoff_intents(step_def)
        behavior = resolve_step_behavior(playbook, step_name)
        context = {
            "agent_file": AgentManager.get_agent_file_path(agent_name, role_dir),
            "handoff_summary": getattr(blackboard_state, "handoff_summary", ""),
            "blackboard_digest": self._build_blackboard_digest(blackboard_state),
            "issue_dir": self._display_path(self.issue_dir),
            "current_step": step_name,
            "iteration_dir": self._display_path(output_file.parent),
            "playbook_id": str(playbook.get("playbook", {}).get("id", "")),
            "blackboard_path": self._display_path(self.issue_dir / "blackboard.json"),
            "next_step_path": self._display_path(baton_path or self.issue_dir / "next_step.txt"),
            "output_file": self._display_path(output_file),
            "valid_to_steps": ", ".join(valid_to_steps),
            "valid_baton_intents": ", ".join(valid_baton_intents),
            "step_transitions": ", ".join(f"{i}→{s}" for i, s in step_transitions.items()),
            "publish_confirmation": behavior.publish_confirmation,
        }

        skill_name = self._resolve_skill_name(step_def, self.iteration)
        contract = self._get_skill_loader().get_workflow_contract(skill_name)
        input_artifacts = self._step_input_artifacts(step_def, blackboard_state)
        try:
            authoritative_inputs = resolve_prompt_inputs(contract, input_artifacts)
        except DeclaredArtifactError as exc:
            raise ValueError(
                f"Step {step_name!r}, skill {canonical_skill_name(skill_name)!r}: {exc}"
            ) from exc
        context["authoritative_inputs"] = {
            placeholder: self._display_path(Path(path))
            for placeholder, path in authoritative_inputs.items()
        }
        feedback = bool(input_artifacts.get("review_feedback") or input_artifacts.get("pr_result"))
        packet_requested_placeholders = self._packet_requested_placeholders(
            contract,
            input_artifacts,
            step=step_name,
            iteration=self.iteration,
            feedback=feedback,
            authoritative_inputs=authoritative_inputs,
        )
        effective_inputs = self._load_persisted_effective_inputs(
            output_file.parent,
            require_persisted_packet_decision=bool(packet_requested_placeholders),
            authoritative_inputs=authoritative_inputs,
            packet_requested_placeholders=packet_requested_placeholders,
            target_step=step_name,
            iteration=self.iteration,
        )
        if effective_inputs is None:
            effective_inputs = resolve_effective_prompt_inputs(
                contract,
                input_artifacts,
                step=step_name,
                iteration=self.iteration,
                feedback=feedback,
                packet_dir=output_file.parent,
            )
            self._persist_context_packet_diagnostics(output_file.parent, effective_inputs)
        context.update(
            {
                placeholder: self._display_path(Path(binding["path"]))
                for placeholder, binding in effective_inputs.items()
            }
        )
        context["input_loading_modes"] = ", ".join(
            f"{placeholder}={binding['mode'] if binding['mode'] != 'full_fallback' else format_context_packet_diagnostic(binding)}"
            for placeholder, binding in sorted(effective_inputs.items())
        )
        self._add_template_context(
            context=context,
            step_name=step_name,
            step_def=step_def,
            skill_name=skill_name,
            contract=contract,
        )

        if "workflow_metadata" in behavior.context_providers:
            context["workflow_metadata"] = json.dumps(
                {
                    "entry_point": str(
                        playbook.get("entry_point") or next(iter(playbook.get("steps", {})), "")
                    ),
                    "playbook_id": str(playbook.get("playbook", {}).get("id", "")),
                    "steps": valid_to_steps[:-2],
                },
                sort_keys=True,
            )

        if "git_history" in behavior.context_providers:
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
                state.handoff_contract.to_dict() if state.handoff_contract is not None else None
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
        feedback = bool(input_artifacts.get("review_feedback") or input_artifacts.get("pr_result"))
        compose_declared_checklist(
            skill_name=canonical_name,
            contract=contract,
            agent_name=agent_name,
            role=str(step_def.get("role", "developer")),
            checklist_file_path=checklist_file,
            step=step_name,
            iteration=self.iteration,
            context=context,
            artifacts=input_artifacts,
            feedback=feedback,
            template_mode=self._resolved_template_mode(step_name, step_def),
            template_file=self._resolved_template_file(
                step_name,
                step_def,
                canonical_name,
                contract,
            ),
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

    def _step_requires_status_code(self, step_name: str) -> bool:
        return resolve_step_behavior(self.playbook, step_name).completion == "status_code"

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

    @staticmethod
    def _declared_human_task_id(step_def: Dict[str, Any], trigger: str) -> Optional[str]:
        """Return the task id selected by a step without inferring its name."""
        raw_tasks = step_def.get("human_tasks")
        if not isinstance(raw_tasks, (list, tuple)):
            return None
        matches = [
            item.get("task_id")
            for item in raw_tasks
            if isinstance(item, dict) and item.get("trigger") == trigger
        ]
        if len(matches) != 1 or not isinstance(matches[0], str) or not matches[0].strip():
            return None
        return matches[0].strip()

    def _agent_wrote_baton(
        self,
        step_name: str,
        step_def: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Check whether the agent already wrote a valid baton (next_step.txt).

        A baton is considered agent-written when it exists, parses as a valid
        contract, uses an intent exposed by the current step, targets a
        different step (from_step != to_step), and is attributed to the current
        step (or defaults to it under the new strict contract).
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
            allowed_handoff_intents = set(effective_step_handoff_intents(step_def or {}))
            return (
                contract.from_step == step_name
                and contract.from_step != contract.to_step
                and contract.to_step
                and contract.intent.value in allowed_handoff_intents
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
        if resolve_step_behavior(self.playbook, step_name).completion == "baton":
            return

        store = BlackboardStore(self.issue_dir)
        task_id = self._declared_human_task_id(
            step_def, self._resolve_handoff_intent(step_def, status_code) or ""
        )

        if status_code == PhaseStatusCode.NO_CHANGES_NEEDED:
            if task_id or self.interactive:
                store.update_handoff_contract(
                    blackboard_state,
                    from_step=step_name,
                    to_owner=HandoffOwner.USER,
                    to_step="user",
                    intent=HandoffIntent.NO_CHANGES_NEEDED,
                    status_code=status_code.value,
                    source="workflow.status_transition_adapter",
                )
                if task_id:
                    store.record_event(
                        blackboard_state,
                        "human_task_requested",
                        {
                            "step": step_name,
                            "trigger": "no_changes_needed",
                            "task_id": task_id,
                        },
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
            if task_id:
                store.record_event(
                    blackboard_state,
                    "human_task_requested",
                    {
                        "step": step_name,
                        "trigger": raw_intent,
                        "task_id": task_id,
                    },
                )
            elif raw_intent in {"confirm_output", "need_clarification"}:
                store.record_event(
                    blackboard_state,
                    "human_task_configuration_error",
                    {
                        "step": step_name,
                        "trigger": raw_intent,
                        "reason": "No declared human-task policy matches this handoff.",
                    },
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
        if resolve_step_behavior(self.playbook, step_name).publish_confirmation:
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
        return declared

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
