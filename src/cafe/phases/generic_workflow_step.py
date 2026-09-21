"""Direct playbook step execution through GenericPhase."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from datetime import datetime, timezone
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
from cafe.core.git import GitError, GitOperations
from cafe.core.human_task_records import (
    HumanTaskRecordError,
    HumanTaskRecordStore,
    HumanTaskStatus,
)
from cafe.core.human_tasks import (
    AGENT_EXECUTION_FRESH_SESSION_DECISION,
    AGENT_EXECUTION_INTERRUPTED_TASK_ID,
    AGENT_EXECUTION_INTERRUPTED_TRIGGER,
)
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
from cafe.core.todo import (
    MAX_TODO_ITEMS,
    PlanTodoDocumentKind,
    TodoContractError,
    TodoSourceArtifact,
    parse_plan_todo_document,
    parse_todo_identity_continuity,
    parse_todo_list,
    plan_work_fingerprint,
    projection_todo_items,
    workflow_feedback_matching_identities,
    workflow_feedback_todo_items,
)
from cafe.core.types import AgentCLI
from cafe.core.workflow_feedback import (
    WorkflowFeedbackLedger,
    feedback_todo_mappings,
)
from cafe.core.workflow_models import BatonRejected, StepExecutionResult
from cafe.core.workspace_artifact import (
    WorkspaceArtifact,
    WorkspaceArtifactError,
    build_workspace_artifact,
    verify_workspace_artifact,
)
from cafe.core.workspace_lock import workspace_execution_lock
from cafe.phases.generic_phase import GenericPhase
from cafe.skills.checklist_composer import (
    compose_declared_checklist,
    generate_custom_skill_checklist,
    select_checklist_variant,
)
from cafe.skills.contracts import (
    DeclaredArtifactError,
    SkillWorkflowDeclaration,
    resolve_effective_prompt_inputs,
    resolve_packet_requested_placeholders,
    resolve_prompt_inputs,
)
from cafe.skills.loader import SkillLoader, canonical_skill_name
from cafe.skills.workflow_composition import resolve_step_workflow_composition
from cafe.templates.manager import TemplateManager
from cafe.utils.checklist_utils import generate_checklist_file
from cafe.utils.checklist_validator import completion_requires_checklist, validate_projected_todos
from cafe.utils.git_utils import get_git_toplevel, get_repo_root, to_cwd_relative_path
from cafe.utils.phase_config import load_phase_step_model


def _plan_work_identity(item: Any) -> str:
    """Hash the retained work payload without making the mutable ID part of it."""
    return plan_work_fingerprint(str(item.work))


_TODO_IDENTITY_BASELINE_SCHEMA_VERSION = 1


def _normalize_todo_identity_baseline(
    value: Any, *, artifact_name: str
) -> dict[str, Any]:
    """Validate and canonicalize a provisional plan's baseline reference."""
    if not isinstance(value, dict):
        raise ValueError(
            f"prior plan Todo authority {artifact_name!r} has malformed baseline metadata"
        )
    if value.get("schema_version") != _TODO_IDENTITY_BASELINE_SCHEMA_VERSION:
        raise ValueError(
            f"prior plan Todo authority {artifact_name!r} has unsupported baseline metadata"
        )
    if "artifact" not in value:
        raise ValueError(
            f"prior plan Todo authority {artifact_name!r} has incomplete baseline metadata"
        )
    raw_artifact = value["artifact"]
    if raw_artifact is None:
        return {
            "schema_version": _TODO_IDENTITY_BASELINE_SCHEMA_VERSION,
            "artifact": None,
        }
    if not isinstance(raw_artifact, dict):
        raise ValueError(
            f"prior plan Todo authority {artifact_name!r} has malformed baseline artifact"
        )
    required_strings = ("name", "kind", "updated_by", "path", "content_sha256")
    for field in required_strings:
        if not isinstance(raw_artifact.get(field), str) or not raw_artifact[field].strip():
            raise ValueError(
                f"prior plan Todo authority {artifact_name!r} has invalid baseline artifact {field}"
            )
    version = raw_artifact.get("version")
    if type(version) is not int or version <= 0:
        raise ValueError(
            f"prior plan Todo authority {artifact_name!r} has invalid baseline artifact version"
        )
    digest = str(raw_artifact["content_sha256"])
    if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ValueError(
            f"prior plan Todo authority {artifact_name!r} has invalid baseline artifact digest"
        )
    if (
        raw_artifact["name"] != artifact_name
        or raw_artifact["kind"] != ArtifactKind.DOCUMENT.value
    ):
        raise ValueError(
            f"prior plan Todo authority {artifact_name!r} has contradictory "
            "baseline artifact identity"
        )
    return {
        "schema_version": _TODO_IDENTITY_BASELINE_SCHEMA_VERSION,
        "artifact": {
            "name": str(raw_artifact["name"]),
            "kind": str(raw_artifact["kind"]),
            "version": version,
            "updated_by": str(raw_artifact["updated_by"]),
            "path": str(raw_artifact["path"]),
            "content_sha256": digest,
        },
    }


def _todo_identity_baseline_reference(artifact: ArtifactEntry) -> dict[str, Any]:
    """Create the exact durable identity needed to reopen plan alignment safely."""
    if not artifact.content_sha256:
        raise ValueError("prior plan Todo authority is missing its content digest")
    return {
        "schema_version": _TODO_IDENTITY_BASELINE_SCHEMA_VERSION,
        "artifact": {
            "name": artifact.name,
            "kind": artifact.kind.value,
            "version": artifact.version,
            "updated_by": artifact.updated_by,
            "path": artifact.path,
            "content_sha256": artifact.content_sha256,
        },
    }


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

    def _effective_workflow_declaration(
        self,
        *,
        step_name: str,
        step_def: Dict[str, Any],
        skill_name: str,
    ) -> SkillWorkflowDeclaration:
        """Resolve supported runtime fields without changing primary-owned rendering."""
        workflow_skills = resolve_playbook_skills(
            self.playbook,
            channel="workflow",
            role=step_def.get("role"),
            step_name=step_name,
        )
        return resolve_step_workflow_composition(
            self._get_skill_loader(),
            primary_skill=skill_name,
            workflow_skills=workflow_skills,
            step_name=step_name,
        ).as_declaration()

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
        open_pr: bool = False,
        config_allowed_directories: Optional[List[str]] = None,
        extra_allowed_directories: Optional[List[str]] = None,
    ) -> None:
        self.interactive = interactive
        self.open_pr = open_pr
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
        self._session_recovery: Optional[Dict[str, Any]] = None
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
            for path, content in snapshots.items():
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
        validated_pr_auto_create: Optional[bool] = None,
    ) -> StepExecutionResult:
        hybrid_portion = step_def.get("hybrid_portion")
        is_hybrid_portion = isinstance(hybrid_portion, Mapping)
        baton_path = self.issue_dir / "next_step.txt"
        self.phase_name = step_name
        self.phase_dir = self.issue_dir / step_name
        self.phase_dir.mkdir(parents=True, exist_ok=True)

        # A playbook rollout can introduce a workspace companion after its
        # producer already completed. Refresh it from the one producer named
        # by the declarations when its committed Git snapshot is stale.
        self._refresh_declared_workspace_input(
            step_def=step_def,
            blackboard_state=blackboard_state,
        )

        self.iteration = self._get_next_iteration_number(step_name, self.phase_dir)
        self._resolved_iteration_user_input = None
        self._session_recovery = None
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
            workflow_id=blackboard_state.workflow_id,
        )
        self._apply_step_agent_model(step_name=step_name, step_def=step_def, agent_name=agent_name)
        effective_agent_config = self._resolve_execution_config_for_iteration(
            agent_name=agent_name,
            step_name=step_name,
            continuation=self._session_continuation,
        )
        context = self._build_context(
            step_name=step_name,
            step_def=step_def,
            blackboard_state=blackboard_state,
            agent_name=agent_name,
            output_file=output_file,
            baton_path=portion_baton_path or baton_path,
            validated_pr_auto_create=validated_pr_auto_create,
        )
        contract = self._get_skill_loader().get_workflow_declaration(skill_name)
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
        managed_skill_names = [*workflow_skill_names, skill_name]
        runtime_agent_clis = self._configured_clis_from_config(effective_agent_config)

        shared_invocations_by_skill: List[Dict[AgentCLI, str]] = [{} for _ in workflow_skill_names]
        phase_invocations: Dict[AgentCLI, str] = {}
        for target_cli in runtime_agent_clis:
            self.generic_phase.skill_bridge.synchronize_skills(
                managed_skill_names,
                target_cli,
                install=False,
            )
            target_shared_invocations = self.generic_phase.prepare_skills(
                skill_names=workflow_skill_names,
                agent_cli=target_cli,
                context=context,
            )
            for index, invocation in enumerate(target_shared_invocations):
                shared_invocations_by_skill[index][target_cli] = invocation
            phase_invocations[target_cli] = self.generic_phase.prepare_skill(
                skill_name=skill_name,
                agent_cli=target_cli,
                context=context,
            )
        shared_skill_invocations = [
            self.generic_phase.skill_bridge.provider_aware_invocation(invocations)
            for invocations in shared_invocations_by_skill
        ]
        skill_invocation = self.generic_phase.skill_bridge.provider_aware_invocation(
            phase_invocations
        )
        # A checklist is a derived phase contract, whether the skill uses the
        # current declaration or the legacy execution_steps convention.
        # Refresh it on resume so a phase update cannot leave an interrupted
        # iteration governed by stale gates.  Unchanged completed items retain
        # their marks; changed and newly declared items remain open.
        self._generate_checklist(
            step_name=step_name,
            skill_name=skill_name,
            agent_name=agent_name,
            step_def=step_def,
            blackboard_state=blackboard_state,
            checklist_file=checklist_file,
            output_file=output_file,
            questions_xml_file=questions_xml_file,
            preserve_completed_items=checklist_file.exists(),
            runtime_context=context,
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
        if self._session_recovery is not None:
            phase_specific_data["session_recovery"] = dict(self._session_recovery)
        require_status_code = self._step_requires_status_code(step_name)

        def run_agent(prompt: str) -> str:
            last_prompt[:] = [prompt]
            self._persist_agent_invocation_marker(
                iteration_dir=iteration_dir,
                agent_invoked=True,
            )
            if self._delta_packet_metadata is not None:
                phase_specific_data["delta_packet"] = dict(self._delta_packet_metadata)
            resolved_user_input = self._get_resolved_iteration_user_input(step_name)
            attempt_allowed_tools = (
                self._build_baton_retry_allowed_tools()
                if self._is_baton_retry_user_input(resolved_user_input)
                else allowed_tools
            )

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
            return response

        def transform_runtime_context(runtime_context: Dict[str, str]) -> Dict[str, str]:
            return self._apply_resume_to_runtime_context(
                runtime_context,
                step_name,
                blackboard_state,
                extra_prompt,
            )

        self._persist_agent_invocation_marker(
            iteration_dir=iteration_dir,
            agent_invoked=False,
        )
        feedback_batch_source_identities: tuple[str, ...] | None = None

        def prepare_agent_context(runtime_context: Dict[str, str]) -> Dict[str, str]:
            """Expose one immutable, bounded feedback batch immediately before prompt."""
            nonlocal feedback_batch_source_identities
            if not feedback_todo_mappings(self.playbook, target_step=step_name):
                return runtime_context
            snapshot_path = iteration_dir / "workflow_feedback_batch.json"
            ledger = WorkflowFeedbackLedger(self.issue_dir)
            feedback_batch_source_identities = ledger.write_pending_snapshot(
                path=snapshot_path,
                target_step=step_name,
                limit=MAX_TODO_ITEMS,
            )
            runtime_context.update(
                {
                    "workflow_feedback_batch_file": self._display_path(snapshot_path),
                    "workflow_feedback_batch_count": str(len(feedback_batch_source_identities)),
                }
            )
            return runtime_context

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
            prepare_agent_context=prepare_agent_context,
            execution_guard=lambda: self._refresh_and_validate_workspace_inputs(
                step_def=step_def,
                blackboard_state=blackboard_state,
            ),
            execution_lease=lambda: workspace_execution_lock(
                Path(getattr(self.git_ops, "repo_path", Path.cwd()))
            ),
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
                "validated_pr_auto_create": validated_pr_auto_create,
                "transform_runtime_context": transform_runtime_context,
            },
        )

        response = execution.response
        status_code = execution.status_code
        if require_status_code:
            if status_code is None:
                status_code = StatusCodeParser.extract(response, valid_intents)

        agent_was_invoked = execution.agent_invoked
        self._persist_agent_invocation_marker(
            iteration_dir=iteration_dir,
            agent_invoked=agent_was_invoked,
        )
        auto_continue = any(
            self._event_allows_auto_continue(event)
            for event in execution.events
            if isinstance(event, dict)
        )
        checklist_validation_required = self._output_requires_contract_validation(
            step_name=step_name,
            status_code=status_code,
            baton_path=portion_baton_path or baton_path,
            hybrid_portion=is_hybrid_portion,
        )
        initial_outbound_validation = (True, "", False)
        if agent_was_invoked:
            initial_outbound_validation = self._validate_outbound_causal_todo(
                step_name=step_name,
                step_def=step_def,
                blackboard_state=blackboard_state,
                output_file=output_file,
                response=response,
                status_code=status_code,
                auto_continue=auto_continue,
            )
        outbound_validation_required = initial_outbound_validation[2]
        checklist_validation_failed = False
        produced_todo_validation_failed = False
        if agent_was_invoked and (
            checklist_validation_required or outbound_validation_required
        ):
            resolved_user_input = self._get_resolved_iteration_user_input(step_name)

            def validate_output_contract(
                current_response: str,
                current_status: Optional[PhaseStatusCode],
            ) -> tuple[bool, str]:
                todo_passed, todo_detail = self._validate_produced_todo_output(
                    output_file
                )
                if not todo_passed:
                    return False, todo_detail
                return self._validate_outbound_causal_todo(
                    step_name=step_name,
                    step_def=step_def,
                    blackboard_state=blackboard_state,
                    output_file=output_file,
                    response=current_response,
                    status_code=current_status,
                    auto_continue=auto_continue,
                )[:2]

            def validate_completion():
                return self._validate_and_retry_checklist_completion(
                    agent_name=agent_name,
                    prompt=last_prompt[0] if last_prompt else "",
                    user_input=resolved_user_input,
                    valid_intents=valid_intents,
                    allowed_tools=allowed_tools,
                    max_retries=3,
                    completion_response=response,
                    completion_status=status_code,
                    validate_checklist_completion=checklist_validation_required,
                    additional_validation=validate_output_contract,
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
            checklist_validation_failed = not validation_passed
            if checklist_validation_failed:
                produced_todo_validation_failed = not self._validate_produced_todo_output(
                    output_file
                )[0]

        store = BlackboardStore(self.issue_dir)
        if produced_todo_validation_failed and not is_hybrid_portion:
            store.update_handoff_contract(
                blackboard_state,
                from_step=step_name,
                to_owner=HandoffOwner.AGENT,
                to_step=step_name,
                intent=HandoffIntent.AWAIT_AGENT,
                status_code="CHECKLIST_VALIDATION_FAILED",
                source="workflow.completion_validation",
            )

        output_key = str(step_def.get("output_artifact", step_name))
        artifacts: Dict[str, str] = {}
        artifact_metadata: Dict[str, Dict[str, Any]] = {}
        if execution.artifact_ready and not checklist_validation_failed and output_file.exists():
            # Context packets are an optional runtime view.  Their structural
            # eligibility is resolved at the consuming edge, where any failure
            # safely selects the complete authoritative artifact.
            output_path = str(output_file)
            artifacts[output_key] = output_path
            summary_record = self._write_artifact_record(
                blackboard_state=blackboard_state,
                output_key=output_key,
                output_path=output_path,
                updated_by=step_name,
            )
            # Carry the complete producer record into the runtime publication
            # boundary.  The transition emitted after publication must bind
            # the same content digest and Todo identities that were written to
            # the iteration artifact record.
            artifact_metadata[output_key] = summary_record.to_dict()
            workspace = self._publish_workspace_artifact(
                step_name=step_name,
                step_def=step_def,
                output_file=output_file,
                blackboard_state=blackboard_state,
                updated_at=summary_record.updated_at,
            )
            if workspace is not None:
                workspace_path, workspace_metadata = workspace
                workspace_key = str(step_def["workspace_artifact"])
                artifacts[workspace_key] = workspace_path
                artifact_metadata[workspace_key] = workspace_metadata

        # READY_FOR_REVIEW / CONFIRM_OUTPUT / NEED_CLARIFICATION always
        # hand off to the user step.  In interactive mode the user sees
        # output and confirm/modify options via _handle_user_phase.  In
        # non-interactive mode the workflow stops and the caller provides
        # input via --user-input.
        # auto_continue stays False → handoff to user.

        effective_status = status_code

        events = [event for event in execution.events if isinstance(event, dict)]
        if checklist_validation_failed:
            events.append(
                {
                    "type": "checklist_validation_failed",
                    "step": step_name,
                    "iteration": self.iteration,
                }
            )
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

        if (
            status_code is not None
            and not is_hybrid_portion
            and not produced_todo_validation_failed
        ):
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
            artifact_ready=execution.artifact_ready and not checklist_validation_failed,
            agent_invoked=agent_was_invoked,
            events=events,
            feedback_source_identities=feedback_batch_source_identities,
            artifact_metadata=artifact_metadata,
        )

    def _persist_agent_invocation_marker(
        self,
        *,
        iteration_dir: Path,
        agent_invoked: bool,
    ) -> None:
        """Persist whether this iteration crossed the agent execution boundary."""
        context_file = self._resolve_iteration_context_file(iteration_dir)
        context_data: object = {}
        if context_file.exists():
            try:
                context_data = json.loads(context_file.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                return
        if not isinstance(context_data, dict):
            return
        iteration_dir.mkdir(parents=True, exist_ok=True)
        context_data["agent_invoked"] = agent_invoked
        context_file.write_text(
            json.dumps(context_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
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
        skill_name = self._resolve_skill_name(step_def, self.iteration)
        primary_contract = self._get_skill_loader().get_workflow_declaration(skill_name)
        contract = self._effective_workflow_declaration(
            step_name=step_name, step_def=step_def, skill_name=skill_name
        )
        input_artifacts = self._step_input_artifacts(step_def, blackboard_state)
        self._prepare_todo_identity_input(
            step_def=step_def,
            input_artifacts=input_artifacts,
        )
        causal_artifact = next(
            (
                section.todo_projection.artifact
                for variant in (
                    primary_contract.checklist.variants if primary_contract.checklist else ()
                )
                for section in variant.sections
                if section.todo_projection and section.todo_projection.causal
            ),
            None,
        )
        if causal_artifact is not None:
            input_artifacts = self._add_causal_todo_artifact(
                input_artifacts,
                blackboard_state,
                playbook=self.playbook,
                causal_artifact=causal_artifact,
            )
        feedback = bool(causal_artifact and input_artifacts.get(causal_artifact))
        authoritative_inputs = resolve_prompt_inputs(contract, input_artifacts)
        packet_requested_placeholders = self._packet_requested_placeholders(
            contract,
            input_artifacts,
            step=step_name,
            iteration=self.iteration,
            feedback=feedback,
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
                feedback=feedback,
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

        snapshot = build_takeover_snapshot(
            reason=error,
            step=step_name,
            iteration=self.iteration,
            resolved_inputs=resolved_inputs,
            output_file=output_file,
            checklist_file=checklist_file,
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
        contract: SkillWorkflowDeclaration,
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

    @staticmethod
    def _persist_todo_projection_snapshot(
        iteration_dir: Path, projections: list[dict[str, Any]]
    ) -> None:
        """Pin the source identity rendered for this invocation in iteration metadata."""
        path = iteration_dir / "iteration.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("Unable to persist Todo projection provenance") from exc
        if not isinstance(raw, dict):
            raise ValueError("Invalid iteration metadata for Todo projection provenance")
        raw["todo_projections"] = projections
        path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _load_todo_projection_snapshot(iteration_dir: Path) -> list[dict[str, Any]] | None:
        path = iteration_dir / "iteration.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        value = raw.get("todo_projections") if isinstance(raw, dict) else None
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            return None
        return value

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
        return self._configured_clis_from_config(config)

    @staticmethod
    def _configured_clis_from_config(config: Any) -> list[AgentCLI]:
        """Return the active CLI followed by every configured failover CLI."""
        configured: list[AgentCLI] = []
        if isinstance(getattr(config, "cli", None), AgentCLI):
            configured.append(config.cli)
        for entry in getattr(config, "clis", None) or []:
            cli = getattr(entry, "cli", None)
            if isinstance(cli, AgentCLI) and cli not in configured:
                configured.append(cli)
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
        workflow_id: Optional[str] = None,
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
            recovery = self._selected_fresh_session_recovery(
                workflow_id=workflow_id,
                current_data=current_data,
            )
            if recovery is not None:
                self._session_recovery = recovery
                return SessionContinuation.new()
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

        if self.iteration > 1 and step_def.get("correction_session", "resume") == "resume":
            exact = exact_continuation_from_context(
                previous_data,
                configured_clis=configured_clis,
            )
            return exact or SessionContinuation.new()

        return SessionContinuation.new()

    def _selected_fresh_session_recovery(
        self,
        *,
        workflow_id: Optional[str],
        current_data: Optional[dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Return the latest applicable user-authorized session rotation."""
        store = HumanTaskRecordStore(self.issue_dir)
        if not workflow_id or not store.exists or not isinstance(current_data, dict):
            return None

        try:
            matching = [
                task
                for task in store.tasks()
                if task.workflow_id == workflow_id
                and task.step == self.phase_name
                and task.iteration == self.iteration
                and task.trigger == AGENT_EXECUTION_INTERRUPTED_TRIGGER
                and task.policy_id == AGENT_EXECUTION_INTERRUPTED_TASK_ID
                and task.status is HumanTaskStatus.COMPLETED
            ]
        except HumanTaskRecordError as exc:
            raise RuntimeError("Cannot validate fresh-session recovery evidence") from exc
        if not matching:
            return None

        latest = max(matching, key=lambda task: (task.completed_at or "", task.id))
        try:
            result = store.get_result(latest.id)
        except HumanTaskRecordError as exc:
            raise RuntimeError("Cannot validate fresh-session recovery result") from exc
        if result is None:
            return None
        if result.payload.get("decision") != AGENT_EXECUTION_FRESH_SESSION_DECISION:
            return None

        declared_decisions = latest.expected_result.get("decisions")
        if not isinstance(declared_decisions, list) or not any(
            isinstance(decision, Mapping)
            and decision.get("id") == AGENT_EXECUTION_FRESH_SESSION_DECISION
            for decision in declared_decisions
        ):
            raise RuntimeError("Fresh-session recovery was not declared by the completed task")
        if latest.continuations.get(AGENT_EXECUTION_FRESH_SESSION_DECISION) != self.phase_name:
            raise RuntimeError("Fresh-session recovery does not target the current step")

        recovery = result.payload.get("session_continuation")
        if not isinstance(recovery, Mapping):
            raise RuntimeError("Fresh-session recovery result has no durable session evidence")
        expected_binding = {
            "policy": "new",
            "workflow_id": workflow_id,
            "human_task_id": latest.id,
            "step": self.phase_name,
            "iteration": self.iteration,
        }
        if any(recovery.get(key) != value for key, value in expected_binding.items()):
            raise RuntimeError("Fresh-session recovery result does not match this workflow run")

        previous = recovery.get("previous")
        if not isinstance(previous, Mapping):
            raise RuntimeError("Fresh-session recovery result has no prior-session identity")
        previous_cli = previous.get("cli")
        previous_session = previous.get("session_id")
        if not isinstance(previous_cli, str) or not previous_cli.strip():
            raise RuntimeError("Fresh-session recovery prior CLI is invalid")
        if not isinstance(previous_session, str) or not previous_session.strip():
            raise RuntimeError("Fresh-session recovery prior session is invalid")

        persisted_recovery = current_data.get("session_recovery")
        if persisted_recovery == dict(recovery):
            current_session = current_data.get("session_id")
            if isinstance(current_session, str) and current_session != previous_session:
                return None
            return dict(recovery)

        if current_data.get("cli") != previous_cli:
            return None
        if current_data.get("session_id") != previous_session:
            return None
        previous_model = previous.get("model")
        current_model = current_data.get("model")
        if (
            isinstance(previous_model, str)
            and previous_model
            and isinstance(current_model, str)
            and current_model
            and current_model != previous_model
        ):
            return None
        return dict(recovery)

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
        from cafe.skills.selectors import resolve_skill_selector

        skill = step_def.get("skill")
        if not isinstance(skill, (str, dict)):
            raise ValueError("Step is missing skill configuration")
        return resolve_skill_selector(skill, iteration)

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

        raise ValueError(f"phase config for '{step_name}' has no primary model")

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
            # Agents write only the routing baton. Blackboard state and audit
            # metadata are runtime-owned and must not be mutated by a phase.
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
        display_path = self._display_path(self.issue_dir / "next_step.txt")
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
        validated_pr_auto_create: Optional[bool] = None,
    ) -> Dict[str, str]:
        role = str(step_def.get("role", "developer"))
        # 這條 playbook 實際可用的 to_step（= 所有 step 名 + 內建 user/done），
        # 與 baton 驗證器一致。注入 prompt 讓 agent 不會憑共用 skill 的範例（如 pr）
        # 猜出本 playbook 不存在的 step。
        playbook = getattr(self, "playbook", {})
        valid_to_steps = list(playbook.get("steps", {}).keys()) + ["user", "done"]
        # 本 step 依 intent 定義的下一步（含 _done → done 正規化），給 agent 明確指向。
        step_on = step_def.get("on", {}) if isinstance(step_def.get("on"), dict) else {}
        behavior = resolve_step_behavior(playbook, step_name)
        _agent_source, agent_content = AgentManager.read_agent_file(agent_name, role)
        materialized_agent = output_file.parent / "context_agent_file.md"
        self._restore_control_file(materialized_agent, agent_content.encode("utf-8"))
        terminal_targets = {"_done", "done"}
        terminal_route_intents = {
            str(intent) for intent, target in step_on.items() if str(target) in terminal_targets
        }
        step_transitions: Dict[str, str] = {}
        for raw_intent, raw_target in step_on.items():
            target = "done" if str(raw_target) in terminal_targets else str(raw_target)
            intent = str(raw_intent)
            if behavior.completion == "baton" and target == "done":
                intent = HandoffIntent.WORKFLOW_COMPLETE.value
            step_transitions[intent] = target
        valid_baton_intents = effective_step_handoff_intents(step_def)
        if behavior.completion == "baton" and terminal_route_intents:
            valid_baton_intents = [
                intent for intent in valid_baton_intents if intent not in terminal_route_intents
            ]
            if HandoffIntent.WORKFLOW_COMPLETE.value not in valid_baton_intents:
                valid_baton_intents.append(HandoffIntent.WORKFLOW_COMPLETE.value)
        context = {
            "agent_file": self._display_path(materialized_agent),
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
            "behavior_completion": behavior.completion,
            "publish_confirmation": behavior.publish_confirmation,
        }
        if getattr(self, "_session_recovery", None) is not None:
            context["session_recovery"] = (
                "The user explicitly selected a fresh provider session after an interruption. "
                "Continue the same phase, iteration, model, and authority. Reconstruct the "
                "current state only from the bounded runtime files and declared inputs in this "
                "prompt; do not assume memory from the previous provider session."
            )
        publication_choice = validated_pr_auto_create
        if publication_choice is None:
            publication_choice = self._get_issue_config_value(
                self.issue_dir / "issue.yaml",
                "pr.auto_create",
            )
        if behavior.publish_confirmation:
            if isinstance(publication_choice, bool):
                context["pr_auto_create"] = str(publication_choice).lower()

        skill_name = self._resolve_skill_name(step_def, self.iteration)
        primary_contract = self._get_skill_loader().get_workflow_declaration(skill_name)
        contract = self._effective_workflow_declaration(
            step_name=step_name, step_def=step_def, skill_name=skill_name
        )
        self._refresh_declared_workspace_input(
            step_def=step_def,
            blackboard_state=blackboard_state,
        )
        input_artifacts = self._step_input_artifacts(step_def, blackboard_state)
        self._prepare_todo_identity_input(
            step_def=step_def,
            input_artifacts=input_artifacts,
        )
        self._validate_workspace_inputs(input_artifacts, step_def=step_def)
        causal_projections = [
            section.todo_projection
            for variant in (
                primary_contract.checklist.variants if primary_contract.checklist else ()
            )
            for section in variant.sections
            if section.todo_projection and section.todo_projection.causal
        ]
        inbound_route = self._persisted_inbound_feedback_route(
            step_name, blackboard_state
        )
        if causal_projections or inbound_route is not None:
            causal_artifact = (
                causal_projections[0].artifact
                if causal_projections
                else inbound_route.artifact
            )
            assert causal_artifact is not None
            input_artifacts = self._add_causal_todo_artifact(
                input_artifacts,
                blackboard_state,
                playbook=self.playbook,
                causal_artifact=causal_artifact,
            )
            feedback = bool(input_artifacts.get(causal_artifact))
        else:
            feedback = False
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
            contract=primary_contract,
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
            comparison_base = resolved_base
            context["base_branch"] = resolved_base
            context["pr_comparison_base"] = comparison_base
            context["commits"] = self._get_current_branch_commits(
                self.git_ops,
                comparison_base,
            )

        if "local_review" in behavior.context_providers:
            base_branch = self._get_issue_config_value(self.issue_dir / "issue.yaml", "base_branch")
            context["review_base"] = str(base_branch or self.git_ops.get_default_base_branch())
            context["review_head"] = "HEAD"
            context["review_required"] = str(publication_choice is not True).lower()

        return context

    def _validate_workspace_inputs(
        self, artifacts: Mapping[str, Any], *, step_def: Optional[Mapping[str, Any]] = None
    ) -> None:
        """Reject stale current-contract workspace companions before agent launch."""
        required_name = (
            step_def.get("workspace_input_artifact") if step_def is not None else None
        )
        if isinstance(required_name, str):
            required_entry = artifacts.get(required_name)
            if required_entry is None:
                raise ValueError(
                    f"required workspace artifact {required_name!r} is missing; refresh the workspace"
                )
            if getattr(required_entry, "kind", None) != ArtifactKind.WORKSPACE:
                raise ValueError(
                    f"required workspace artifact {required_name!r} has the wrong kind"
                )
        summary_name = (
            step_def.get("output_artifact") if step_def is not None else None
        )
        workspace_entries: list[tuple[str, Any, Path]] = []
        for name, entry in artifacts.items():
            if getattr(entry, "kind", None) != ArtifactKind.WORKSPACE:
                continue
            path = Path(str(getattr(entry, "path", entry)))
            if not isinstance(required_name, str) and path.name != "workspace.json":
                # Bounded v0.2 adapter: a mixed code/workspace record remains
                # readable for legacy consumers, but is never certified as a
                # current Git workspace.
                continue
            workspace_entries.append((name, entry, path))

        if not workspace_entries:
            return

        active_repo = Path(getattr(self.git_ops, "repo_path", Path.cwd())).resolve()
        for name, entry, path in workspace_entries:
            try:
                workspace = WorkspaceArtifact.from_dict(
                    json.loads(path.read_text(encoding="utf-8"))
                )
            except (OSError, UnicodeError, json.JSONDecodeError, WorkspaceArtifactError) as exc:
                raise ValueError(
                    f"workspace artifact {name!r} is missing or malformed; refresh it"
                ) from exc
            if workspace.name != name or getattr(entry, "name", name) != workspace.name:
                raise ValueError(
                    f"workspace artifact {name!r} has contradictory logical identity"
                )
            if summary_name is not None and name == summary_name:
                raise ValueError("workspace artifact must be distinct from the summary artifact")
            if getattr(entry, "version", workspace.version) != workspace.version:
                raise ValueError(f"workspace artifact {name!r} version is contradictory")
            if getattr(entry, "base_sha", workspace.base_sha) != workspace.base_sha:
                raise ValueError(f"workspace artifact {name!r} base SHA is contradictory")
            if getattr(entry, "head_sha", workspace.head_sha) != workspace.head_sha:
                raise ValueError(f"workspace artifact {name!r} head SHA is contradictory")
            checked = verify_workspace_artifact(
                workspace,
                repo=active_repo,
            )
            if not checked.valid:
                detail = "; ".join(checked.reasons)
                raise ValueError(
                    f"workspace artifact {name!r} is stale or contradictory: {detail}; "
                    "refresh it or resolve the conflict before retrying"
                )

    def _refresh_declared_workspace_input(
        self,
        *,
        step_def: Mapping[str, Any],
        blackboard_state: BlackboardState,
        workspace_locked: bool = False,
    ) -> None:
        """Refresh one stale declared workspace snapshot from clean Git facts.

        A workspace companion is a convenience snapshot for consumers, not a
        second authentication system. A prior committed snapshot whose only
        problem is an older HEAD is rebuilt from its declared producer. A dirty
        worktree, malformed record, identity mismatch, or divergent base
        remains an actionable conflict and is never overwritten silently.
        """
        required_name = step_def.get("workspace_input_artifact")
        if not isinstance(required_name, str) or not required_name.strip():
            return
        repo = Path(getattr(self.git_ops, "repo_path", Path.cwd())).resolve()

        def refresh_under_lock() -> None:
            previous = blackboard_state.artifacts.get(required_name)
            previous_workspace: WorkspaceArtifact | None = None
            if previous is not None:
                if getattr(previous, "kind", None) != ArtifactKind.WORKSPACE:
                    raise ValueError(
                        f"workspace artifact {required_name!r} conflicts with its declared kind"
                    )
                workspace_path = Path(str(previous.path))
                if not workspace_path.is_absolute():
                    workspace_path = repo / workspace_path
                try:
                    workspace_path = workspace_path.resolve(strict=True)
                    workspace_path.relative_to(self.issue_dir.resolve())
                    previous_workspace = WorkspaceArtifact.from_dict(
                        json.loads(workspace_path.read_text(encoding="utf-8"))
                    )
                except (
                    OSError,
                    UnicodeError,
                    ValueError,
                    json.JSONDecodeError,
                    WorkspaceArtifactError,
                ) as exc:
                    raise ValueError(
                        f"workspace artifact {required_name!r} is malformed and cannot be "
                        "refreshed automatically"
                    ) from exc
                if (
                    getattr(previous, "name", required_name) != required_name
                    or previous_workspace.name != required_name
                    or getattr(previous, "version", previous_workspace.version)
                    != previous_workspace.version
                    or getattr(previous, "base_sha", previous_workspace.base_sha)
                    != previous_workspace.base_sha
                    or getattr(previous, "head_sha", previous_workspace.head_sha)
                    != previous_workspace.head_sha
                    or previous_workspace.repository != str(repo)
                ):
                    raise ValueError(
                        f"workspace artifact {required_name!r} has contradictory identity"
                    )
                checked = verify_workspace_artifact(previous_workspace, repo=repo)
                if checked.valid:
                    return
                if checked.reasons != ("workspace head is stale",):
                    detail = "; ".join(checked.reasons)
                    raise ValueError(
                        f"workspace artifact {required_name!r} conflicts with the current "
                        f"workspace: {detail}; resolve the conflict before retrying"
                    )

            candidates: list[tuple[str, Mapping[str, Any], ArtifactEntry]] = []
            steps = self.playbook.get("steps", {})
            if isinstance(steps, Mapping):
                for producer_name, producer_def in steps.items():
                    if not isinstance(producer_def, Mapping):
                        continue
                    if producer_def.get("workspace_artifact") != required_name:
                        continue
                    summary_name = str(producer_def.get("output_artifact", producer_name))
                    summary = blackboard_state.artifacts.get(summary_name)
                    if summary is not None and summary.updated_by == str(producer_name):
                        candidates.append((str(producer_name), producer_def, summary))
            if len(candidates) != 1:
                if previous is not None:
                    reason = "no" if not candidates else "multiple"
                    raise ValueError(
                        f"workspace artifact {required_name!r} is stale but has {reason} "
                        "unambiguous declared producer to refresh it"
                    )
                return

            producer_name, producer_def, summary = candidates[0]
            if previous_workspace is not None and (
                previous.updated_by != producer_name
                or previous_workspace.producer_step not in {"", producer_name}
            ):
                raise ValueError(
                    f"workspace artifact {required_name!r} conflicts with its declared producer"
                )
            output_file = Path(summary.path)
            if not output_file.is_absolute():
                output_file = repo / output_file
            try:
                output_file = output_file.resolve(strict=True)
                relative = output_file.relative_to(self.issue_dir.resolve())
            except (OSError, ValueError) as exc:
                raise ValueError(
                    f"cannot refresh required workspace artifact {required_name!r}: "
                    "the declared producer output is outside the issue"
                ) from exc
            if (
                len(relative.parts) != 3
                or relative.parts[0] != producer_name
                or re.fullmatch(r"iteration_[0-9]+", relative.parts[1]) is None
                or relative.parts[2] != "output.md"
            ):
                raise ValueError(
                    f"cannot refresh required workspace artifact {required_name!r}: "
                    "the declared producer output path is not canonical"
                )

            refreshed = self._publish_workspace_artifact_under_lock(
                step_name=producer_name,
                step_def=dict(producer_def),
                output_file=output_file,
                blackboard_state=blackboard_state,
                updated_at=summary.updated_at,
            )
            if refreshed is None:
                return
            workspace_path, metadata = refreshed
            blackboard_state.artifacts[required_name] = ArtifactEntry(
                name=required_name,
                kind=ArtifactKind.WORKSPACE,
                version=int(metadata["version"]),
                updated_by=producer_name,
                path=workspace_path,
                updated_at=str(metadata["updated_at"]),
                base_sha=str(metadata["base_sha"]),
                head_sha=str(metadata["head_sha"]),
            )
            BlackboardStore(self.issue_dir).save(blackboard_state)

        if workspace_locked:
            refresh_under_lock()
        else:
            with workspace_execution_lock(repo):
                refresh_under_lock()

    def _recover_declared_workspace_input(
        self,
        *,
        step_def: Mapping[str, Any],
        blackboard_state: BlackboardState,
    ) -> None:
        """Compatibility alias for callers using the former recovery helper."""
        self._refresh_declared_workspace_input(
            step_def=step_def,
            blackboard_state=blackboard_state,
        )

    def _refresh_and_validate_workspace_inputs(
        self,
        *,
        step_def: Mapping[str, Any],
        blackboard_state: BlackboardState,
    ) -> None:
        """Refresh a stale consumer snapshot while GenericPhase holds its lease."""
        self._refresh_declared_workspace_input(
            step_def=step_def,
            blackboard_state=blackboard_state,
            workspace_locked=True,
        )
        self._validate_workspace_inputs(
            self._step_input_artifacts(step_def, blackboard_state),
            step_def=step_def,
        )

    def _declared_feedback_route_artifact(self, destination: str) -> Optional[str]:
        """Return the artifact declared for the persisted destination edge."""
        for producer_name in (self.playbook.get("steps", {}) or {}):
            routes = resolve_step_behavior(self.playbook, str(producer_name)).feedback_routes or {}
            route = routes.get(destination)
            if route is not None:
                return str(route.artifact)
        return None

    def _persisted_inbound_feedback_route(
        self, destination: str, state: BlackboardState
    ) -> Any:
        """Resolve a correction route only from the recorded sender/destination edge."""
        transition = next(
            (
                event
                for event in reversed(state.events)
                if event.event_type == "transition"
                and event.data.get("to") == destination
            ),
            None,
        )
        from_step = str(transition.data.get("from")) if transition is not None else None
        if state.handoff_contract is not None:
            candidate = state.handoff_contract.from_step
            if candidate and candidate != destination:
                from_step = candidate
        if not from_step:
            return None
        behavior = resolve_step_behavior(self.playbook, from_step)
        return (behavior.feedback_routes or {}).get(destination)

    @staticmethod
    def _persist_plan_artifact_record(record_path: Path, record: dict[str, Any]) -> None:
        temporary = record_path.with_name(f".{record_path.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temporary, record_path)

    @staticmethod
    def _load_validated_plan_artifact(
        artifact_name: str,
        artifact: ArtifactEntry,
        *,
        allow_missing_legacy_record: bool = False,
    ) -> tuple[Any, dict[str, Any], Path, str]:
        """Load one plan artifact and verify its selected and sibling identities."""
        def invalid_selected(field: str) -> ValueError:
            return ValueError(
                f"prior plan Todo authority {artifact_name!r} has an invalid mandatory "
                f"selected artifact {field}; restore the authoritative prior plan before continuing"
            )

        if artifact.name != artifact_name:
            raise invalid_selected("name")
        if artifact.kind is not ArtifactKind.DOCUMENT:
            raise invalid_selected("kind")
        if type(artifact.version) is not int or artifact.version <= 0:
            raise invalid_selected("version")
        if not isinstance(artifact.updated_by, str) or not artifact.updated_by.strip():
            raise invalid_selected("owner")
        if not isinstance(artifact.path, str) or not artifact.path.strip():
            raise invalid_selected("path")
        artifact_path = Path(artifact.path)
        try:
            artifact_bytes = artifact_path.read_bytes()
            content = artifact_bytes.decode("utf-8")
            document = parse_plan_todo_document(content)
        except (OSError, UnicodeError, TodoContractError) as exc:
            raise ValueError(
                f"prior plan Todo authority {artifact_name!r} is unreadable or invalid; "
                "restore the authoritative prior plan before continuing"
            ) from exc
        actual_digest = hashlib.sha256(artifact_bytes).hexdigest()
        if artifact.content_sha256 is not None and artifact.content_sha256 != actual_digest:
            raise ValueError(
                f"prior plan Todo authority {artifact_name!r} has a contradictory "
                "content digest; restore the authoritative prior plan before continuing"
            )
        record_path = artifact_path.parent / "artifact.json"
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            if (
                allow_missing_legacy_record
                and document.kind is PlanTodoDocumentKind.TODO_AUTHORITY
                and artifact.todo_identity_baseline is None
            ):
                record = artifact.to_dict()
                record["content_sha256"] = actual_digest
            else:
                raise ValueError(
                    f"prior plan Todo authority {artifact_name!r} has no readable artifact record; "
                    "restore artifact.json before continuing"
                ) from exc
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"prior plan Todo authority {artifact_name!r} has no readable artifact record; "
                "restore artifact.json before continuing"
            ) from exc
        if not isinstance(record, dict):
            raise ValueError(
                f"prior plan Todo authority {artifact_name!r} has an invalid artifact record; "
                "restore artifact.json before continuing"
            )

        def invalid_record(field: str) -> ValueError:
            return ValueError(
                f"prior plan Todo authority {artifact_name!r} has an invalid mandatory "
                f"artifact {field}; restore artifact.json before continuing"
            )

        if record.get("name") != artifact_name:
            raise invalid_record("name")
        if record.get("kind") != ArtifactKind.DOCUMENT.value:
            raise invalid_record("kind")
        if type(record.get("version")) is not int or record["version"] <= 0:
            raise invalid_record("version")
        if record["version"] != artifact.version:
            raise invalid_record("version")
        if not isinstance(record.get("updated_by"), str) or not record["updated_by"].strip():
            raise invalid_record("owner")
        if record["updated_by"] != artifact.updated_by:
            raise invalid_record("owner")
        if not isinstance(record.get("path"), str) or not record["path"].strip():
            raise invalid_record("path")
        if Path(record["path"]).resolve() != artifact_path.resolve():
            raise invalid_record("path")
        if record.get("content_sha256") is not None and record["content_sha256"] != actual_digest:
            raise ValueError(
                f"prior plan Todo authority {artifact_name!r} has a contradictory "
                "content digest; restore artifact.json before continuing"
            )
        artifact.content_sha256 = actual_digest
        return document, record, record_path, actual_digest

    @staticmethod
    def _normalized_identity_mapping(
        value: Any, *, artifact_name: str, field: str
    ) -> Optional[dict[str, str]]:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError(
                f"prior plan Todo authority {artifact_name!r} has malformed "
                f"{field} identity metadata; restore artifact.json before continuing"
            )
        return {str(key): str(item) for key, item in value.items()}

    @classmethod
    def _validate_direct_plan_todo_authority(
        cls,
        artifact_name: str,
        artifact: ArtifactEntry,
        *,
        allow_missing_legacy_record: bool = False,
    ) -> tuple[ArtifactEntry, tuple[Any, ...]]:
        document, record, record_path, _actual_digest = cls._load_validated_plan_artifact(
            artifact_name,
            artifact,
            allow_missing_legacy_record=allow_missing_legacy_record,
        )
        if document.kind is not PlanTodoDocumentKind.TODO_AUTHORITY:
            raise ValueError(
                f"prior plan Todo authority {artifact_name!r} baseline points to a provisional plan"
            )
        if (
            artifact.todo_identity_baseline is not None
            or "todo_identity_baseline" in record
        ):
            raise ValueError(
                f"prior plan Todo authority {artifact_name!r} has contradictory baseline metadata"
            )
        if any(item.source != "plan" for item in document.items):
            raise ValueError(
                f"prior plan Todo authority {artifact_name!r} contains a non-plan item; "
                "restore the authoritative prior plan before continuing"
            )
        expected_work = {
            plan_work_fingerprint(item.work): item.item_id for item in document.items
        }
        expected_todo = {
            item.item_id: hashlib.sha256(
                "\x1f".join(
                    (item.source, item.item_id, " ".join(item.work.split()))
                ).encode("utf-8")
            ).hexdigest()
            for item in document.items
        }
        selected_work = cls._normalized_identity_mapping(
            artifact.todo_work_identities, artifact_name=artifact_name, field="work"
        )
        selected_todo = cls._normalized_identity_mapping(
            artifact.todo_identities, artifact_name=artifact_name, field="Todo"
        )
        recorded_work = cls._normalized_identity_mapping(
            record.get("todo_work_identities"), artifact_name=artifact_name, field="work"
        )
        recorded_todo = cls._normalized_identity_mapping(
            record.get("todo_identities"), artifact_name=artifact_name, field="Todo"
        )
        for candidate, expected in (
            (selected_work, expected_work),
            (selected_todo, expected_todo),
            (recorded_work, expected_work),
            (recorded_todo, expected_todo),
        ):
            if candidate is not None and candidate != expected:
                raise ValueError(
                    f"prior plan Todo authority {artifact_name!r} has contradictory "
                    "identity metadata; "
                    "restore the authoritative prior plan before continuing"
                )
        if recorded_work is None:
            record["todo_work_identities"] = expected_work
            cls._persist_plan_artifact_record(record_path, record)
        artifact.todo_work_identities = expected_work
        return artifact, tuple(document.items)

    @classmethod
    def _resolve_plan_todo_authority(
        cls,
        artifact_name: str,
        artifact: ArtifactEntry,
        *,
        allow_missing_legacy_record: bool = False,
    ) -> tuple[Optional[ArtifactEntry], tuple[Any, ...]]:
        """Resolve the last detailed plan through any provisional alignment."""
        document, record, record_path, _actual_digest = cls._load_validated_plan_artifact(
            artifact_name,
            artifact,
            allow_missing_legacy_record=allow_missing_legacy_record,
        )
        if document.kind is PlanTodoDocumentKind.TODO_AUTHORITY:
            return cls._validate_direct_plan_todo_authority(
                artifact_name,
                artifact,
                allow_missing_legacy_record=allow_missing_legacy_record,
            )

        for candidate in (
            artifact.todo_identities,
            artifact.todo_work_identities,
            record.get("todo_identities"),
            record.get("todo_work_identities"),
        ):
            if candidate is not None:
                raise ValueError(
                    f"prior plan Todo authority {artifact_name!r} provisional plan "
                    "has identity metadata"
                )
        selected_baseline = (
            _normalize_todo_identity_baseline(
                artifact.todo_identity_baseline, artifact_name=artifact_name
            )
            if artifact.todo_identity_baseline is not None
            else None
        )
        recorded_baseline = (
            _normalize_todo_identity_baseline(
                record["todo_identity_baseline"], artifact_name=artifact_name
            )
            if "todo_identity_baseline" in record
            else None
        )
        if (
            selected_baseline is not None
            and recorded_baseline is not None
            and selected_baseline != recorded_baseline
        ):
            raise ValueError(
                f"prior plan Todo authority {artifact_name!r} has contradictory baseline metadata"
            )
        baseline = selected_baseline or recorded_baseline
        if baseline is None:
            if artifact.version != 1:
                raise ValueError(
                    f"prior plan Todo authority {artifact_name!r} has no baseline metadata"
                )
            baseline = {
                "schema_version": _TODO_IDENTITY_BASELINE_SCHEMA_VERSION,
                "artifact": None,
            }
        if recorded_baseline is None:
            record["todo_identity_baseline"] = baseline
            cls._persist_plan_artifact_record(record_path, record)
        artifact.todo_identity_baseline = baseline
        reference = baseline["artifact"]
        if reference is None:
            return None, ()
        if reference["version"] >= artifact.version:
            raise ValueError(
                f"prior plan Todo authority {artifact_name!r} baseline is not an older version"
            )
        referenced = ArtifactEntry(
            name=reference["name"],
            kind=ArtifactKind(reference["kind"]),
            version=reference["version"],
            updated_by=reference["updated_by"],
            path=reference["path"],
            content_sha256=reference["content_sha256"],
        )
        return cls._validate_direct_plan_todo_authority(artifact_name, referenced)

    @classmethod
    def _prepare_todo_identity_input(
        cls,
        *,
        step_def: Dict[str, Any],
        input_artifacts: Dict[str, Any],
    ) -> None:
        """Materialize and verify the declared prior PLAN identity authority."""
        artifact_name = step_def.get("todo_identity_input_artifact")
        if not isinstance(artifact_name, str) or not artifact_name.strip():
            return
        prior = input_artifacts.get(artifact_name)
        if prior is None:
            return
        if not isinstance(prior, ArtifactEntry):
            raise ValueError(
                f"prior plan Todo authority {artifact_name!r} has an invalid selected artifact"
            )
        cls._resolve_plan_todo_authority(artifact_name, prior)

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
        preserve_completed_items: bool = False,
        runtime_context: Optional[Mapping[str, str]] = None,
    ) -> None:
        canonical_name = canonical_skill_name(skill_name)
        contract = self._get_skill_loader().get_workflow_declaration(skill_name)
        input_contract = self._effective_workflow_declaration(
            step_name=step_name, step_def=step_def, skill_name=skill_name
        )
        input_artifacts = self._step_input_artifacts(step_def, blackboard_state)
        self._validate_workspace_inputs(input_artifacts, step_def=step_def)
        declares_causal_todo = bool(
            contract.checklist
            and any(
                section.todo_projection and section.todo_projection.causal
                for variant in contract.checklist.variants
                for section in variant.sections
            )
        )
        causal_artifact = next(
            (
                section.todo_projection.artifact
                for variant in (contract.checklist.variants if contract.checklist else ())
                for section in variant.sections
                if section.todo_projection and section.todo_projection.causal
            ),
            None,
        )
        inbound_route = self._persisted_inbound_feedback_route(step_name, blackboard_state)
        if declares_causal_todo or inbound_route is not None:
            causal_artifact = causal_artifact or inbound_route.artifact
            assert causal_artifact is not None
            input_artifacts = self._add_causal_todo_artifact(
                input_artifacts,
                blackboard_state,
                playbook=self.playbook,
                causal_artifact=causal_artifact,
            )
        try:
            declared_inputs = resolve_prompt_inputs(input_contract, input_artifacts)
        except DeclaredArtifactError as exc:
            raise ValueError(f"Step {step_name!r}, skill {canonical_name!r}: {exc}") from exc

        previous_output = ""
        if self.iteration > 1:
            previous_output = self._display_path(
                self._get_versioned_file_path(step_name, self.iteration - 1, self.phase_dir)
            )
        context = dict(runtime_context or {})
        context.update(
            {
                placeholder: self._display_path(Path(path))
                for placeholder, path in declared_inputs.items()
            }
        )
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
        causal_entry = input_artifacts.get(causal_artifact) if causal_artifact else None
        if causal_entry is not None:
            context.setdefault(
                "feedback_file",
                self._display_path(Path(str(getattr(causal_entry, "path", causal_entry)))),
            )
        feedback = bool(causal_entry)
        if contract.checklist is None:
            generated = generate_custom_skill_checklist(
                skill_name=canonical_name,
                agent_name=agent_name,
                role=str(step_def.get("role", "developer")),
                checklist_file_path=checklist_file,
                correction_mode=feedback,
                placeholders=context,
                preserve_completed_items=preserve_completed_items,
            )
            if not generated and not checklist_file.exists():
                generate_checklist_file(checklist_file, "")
            return
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
            preserve_completed_items=preserve_completed_items,
            todo_ledger_path=output_file,
        )
        variant = select_checklist_variant(
            contract,
            step=step_name,
            iteration=self.iteration,
            artifacts=input_artifacts,
            feedback=feedback,
        )
        projections: list[dict[str, Any]] = []
        for section in variant.sections:
            declaration = section.todo_projection
            if declaration is None:
                continue
            entry = input_artifacts.get(declaration.artifact)
            if entry is None:
                raise ValueError("Todo projection artifact disappeared during generation")
            items = projection_todo_items(entry, expected_source=declaration.source)
            projections.append(
                {
                    "declaration_artifact": declaration.artifact,
                    "artifact": str(
                        getattr(entry, "artifact", getattr(entry, "name", declaration.artifact))
                    ),
                    "path": str(getattr(entry, "path", entry)),
                    "version": getattr(entry, "version", None),
                    "rows": [item.checklist_row() for item in items],
                }
            )
        self._persist_todo_projection_snapshot(output_file.parent, projections)

    @staticmethod
    def _add_causal_todo_artifact(
        artifacts: Dict[str, Any],
        state: BlackboardState,
        *,
        playbook: Mapping[str, Any] | None = None,
        causal_artifact: str = "causal_todo",
    ) -> Dict[str, Any]:
        """Expose only the artifact selected by the persisted inbound transition."""
        if not artifacts:
            return artifacts

        transition = next(
            (
                event
                for event in reversed(state.events)
                if event.event_type == "transition" and event.data.get("to") == state.current_step
            ),
            None,
        )
        from_step = str(transition.data.get("from")) if transition is not None else None
        if state.handoff_contract is not None:
            candidate = state.handoff_contract.from_step
            if candidate and candidate != state.current_step:
                from_step = candidate

        playbook_data: Dict[str, Any] = dict(playbook or {})
        steps = playbook_data.get("steps", {})
        if not isinstance(steps, Mapping):
            steps = {}
        producer = steps.get(from_step, {})
        direct_artifact = producer.get("output_artifact") if isinstance(producer, Mapping) else None
        selected = artifacts.get(direct_artifact) if isinstance(direct_artifact, str) else None

        # Current correction declarations are resolved from the persisted
        # sender/destination edge before any legacy feedback inference runs.
        current_behavior = resolve_step_behavior(playbook_data, from_step) if from_step else None
        declared_route = (
            (current_behavior.feedback_routes or {}).get(state.current_step)
            if current_behavior is not None
            else None
        )
        if declared_route is not None:
            route_artifact = str(declared_route.artifact)
            selected = artifacts.get(route_artifact)
            if selected is None:
                raise ValueError(
                    f"Correction Todo source {route_artifact!r} is missing for "
                    f"the persisted edge {from_step!r}->{state.current_step!r}"
                )
            if getattr(selected, "updated_by", from_step) != from_step:
                raise ValueError(
                    "Correction Todo source ownership conflicts with the persisted edge"
                )
            prefix = f"{declared_route.todo_id_prefix}-"
            authorized_artifact = (
                transition.data.get("source_artifact")
                if transition is not None and isinstance(transition.data, Mapping)
                else None
            )
            if authorized_artifact is not None and not isinstance(authorized_artifact, Mapping):
                raise ValueError("Persisted correction handoff has an invalid source artifact")
            authorized_name = (
                str(authorized_artifact.get("name"))
                if isinstance(authorized_artifact, Mapping)
                and authorized_artifact.get("name") is not None
                else None
            )
            authorized_version = (
                authorized_artifact.get("version")
                if isinstance(authorized_artifact, Mapping)
                else None
            )
            authorized_path = (
                Path(str(authorized_artifact.get("path"))).resolve()
                if isinstance(authorized_artifact, Mapping)
                and authorized_artifact.get("path")
                else None
            )
            authorized_digest = (
                str(authorized_artifact.get("content_sha256"))
                if isinstance(authorized_artifact, Mapping)
                and authorized_artifact.get("content_sha256")
                else None
            )
            if authorized_artifact is None:
                raise ValueError(
                    "Persisted correction handoff is missing its bound source artifact"
                )
            if (
                authorized_name != route_artifact
                or not isinstance(authorized_version, int)
                or not authorized_path
                or not authorized_digest
            ):
                raise ValueError(
                    "Persisted correction handoff does not bind a complete source artifact"
                )
            selected_path = Path(str(getattr(selected, "path", selected)))
            candidates: list[tuple[int, Path, str]] = []
            phase_dir = selected_path.parent.parent
            iteration_dirs = (
                sorted(phase_dir.glob("iteration_*"), reverse=True)
                if phase_dir.is_dir()
                else []
            )
            for iteration_dir in iteration_dirs[:32]:
                artifact_record = iteration_dir / "artifact.json"
                output_path = iteration_dir / "output.md"
                try:
                    raw_record = json.loads(artifact_record.read_text(encoding="utf-8"))
                    record = ArtifactEntry.from_dict(raw_record)
                except (OSError, UnicodeError, json.JSONDecodeError, KeyError, ValueError):
                    continue
                if (
                    record.name != route_artifact
                    or record.updated_by != from_step
                    or Path(record.path).resolve() != output_path.resolve()
                    or not record.content_sha256
                    or not output_path.is_file()
                    or output_path.stat().st_size > 256 * 1024
                ):
                    continue
                if authorized_artifact is not None and (
                    record.name != authorized_name
                    or record.version != authorized_version
                    or Path(record.path).resolve() != authorized_path
                    or record.content_sha256 != authorized_digest
                ):
                    continue
                digest = hashlib.sha256(output_path.read_bytes()).hexdigest()
                if digest != record.content_sha256:
                    continue
                candidates.append((record.version, output_path, record.updated_by))
            for _version, candidate_path, owner in sorted(
                candidates, key=lambda item: (item[0], str(item[1])), reverse=True
            ):
                if owner != from_step:
                    continue
                try:
                    items = parse_todo_list(
                        candidate_path.read_text(encoding="utf-8"),
                        expected_source=str(declared_route.todo_source),
                    )
                except (OSError, UnicodeError, TodoContractError):
                    continue
                if any(not item.item_id.startswith(prefix) for item in items):
                    continue
                resolved = dict(artifacts)
                resolved[causal_artifact] = TodoSourceArtifact(
                    artifact=route_artifact,
                    source=str(declared_route.todo_source),
                    path=candidate_path,
                    version=_version or getattr(selected, "version", None),
                    items=items,
                )
                return resolved
            raise ValueError(
                "Correction Todo source is incomplete or malformed; publish a complete source"
            )

        routes: list[dict[str, str]] = []
        for producer_name, raw_step in steps.items():
            if not isinstance(raw_step, Mapping):
                continue
            behavior = resolve_step_behavior(playbook_data, str(producer_name))
            if behavior.feedback_target == state.current_step:
                values = {
                    "producer": str(producer_name),
                    "artifact": behavior.feedback_artifact,
                    "source_kind": behavior.feedback_source_kind,
                    "todo_source": behavior.feedback_todo_source,
                    "todo_id_prefix": behavior.feedback_todo_id_prefix,
                }
                if all(isinstance(value, str) and value for value in values.values()):
                    routes.append({key: str(value) for key, value in values.items()})
            for binding in raw_step.get("human_tasks", ()) or ():
                if not isinstance(binding, Mapping):
                    continue
                targets = [
                    *(binding.get("outcomes", {}) or {}).values(),
                    *(binding.get("allowed_targets", ()) or ()),
                ]
                delivery = binding.get("feedback_delivery")
                if state.current_step not in targets or not isinstance(delivery, Mapping):
                    continue
                values = {
                    "producer": str(producer_name),
                    "task_id": binding.get("task_id"),
                    "artifact": delivery.get("artifact"),
                    "source_kind": delivery.get("source_kind"),
                    "todo_source": delivery.get("todo_source"),
                    "todo_id_prefix": delivery.get("todo_id_prefix"),
                }
                if all(isinstance(value, str) and value for value in values.values()):
                    routes.append({key: str(value) for key, value in values.items()})

        delivered: tuple[str, ...] | None = None
        if transition is not None:
            event = next(
                (
                    candidate
                    for candidate in reversed(state.events)
                    if candidate.event_type == "workflow_feedback_delivered"
                    and candidate.step == state.current_step
                    and candidate.timestamp >= transition.timestamp
                ),
                None,
            )
            if event is not None:
                raw = event.data.get("source_identities")
                if (
                    not isinstance(raw, list)
                    or any(not isinstance(item, str) or not item for item in raw)
                    or len(set(raw)) != len(raw)
                ):
                    raise ValueError("Correction Todo delivery identities are invalid")
                delivered = tuple(raw)

        human_task_identities: tuple[str, ...] | None = None
        selected_route: dict[str, str] | None = None
        if from_step is not None:
            human_task_event = next(
                (
                    candidate
                    for candidate in reversed(state.events)
                    if candidate.event_type == "human_task_completed"
                    and candidate.step == from_step
                    and (transition is None or candidate.timestamp >= transition.timestamp)
                    and candidate.data.get("to_step") == state.current_step
                    and isinstance(candidate.data.get("task_id"), str)
                ),
                None,
            )
            if human_task_event is not None:
                task_id = str(human_task_event.data["task_id"])
                matching_routes = [
                    route
                    for route in routes
                    if route.get("producer") == from_step and route.get("task_id") == task_id
                ]
                if len(matching_routes) != 1:
                    raise ValueError("Correction Todo feedback route is ambiguous")
                selected_route = matching_routes[0]
                workflow_entry = artifacts.get(selected_route["artifact"])
                if workflow_entry is None:
                    raise ValueError("Correction Todo feedback artifact is missing")
                try:
                    human_task_identities = workflow_feedback_matching_identities(
                        Path(str(getattr(workflow_entry, "path", workflow_entry))),
                        target_step=state.current_step,
                        source_kind=selected_route["source_kind"],
                        identity_prefix=(
                            f"{selected_route['source_kind']}:{from_step}:{task_id}:"
                        ),
                    )
                except TodoContractError as exc:
                    raise ValueError(f"Correction Todo provenance is invalid: {exc}") from exc

        candidate_routes = [
            route
            for route in routes
            if (from_step is None or route["producer"] == from_step)
            and route["artifact"] in artifacts
        ]
        if selected_route is None and (delivered is not None or selected is None):
            artifacts_for_routes = {route["artifact"] for route in candidate_routes}
            if len(artifacts_for_routes) > 1:
                raise ValueError("Correction Todo feedback artifact is ambiguous")
            if candidate_routes:
                selected_route = candidate_routes[0]
        workflow_entry = (
            artifacts.get(selected_route["artifact"]) if selected_route is not None else None
        )
        use_workflow = workflow_entry is not None and (
            delivered is not None or human_task_identities is not None or selected is None
        )
        if use_workflow:
            assert selected_route is not None
            source_by_kind: dict[str, str] = {}
            id_prefix_by_kind: dict[str, str] = {}
            for route in candidate_routes:
                if route["artifact"] != selected_route["artifact"]:
                    continue
                previous = source_by_kind.setdefault(
                    route["source_kind"], route["todo_source"]
                )
                if previous != route["todo_source"]:
                    raise ValueError("Correction Todo source mapping is ambiguous")
                previous_prefix = id_prefix_by_kind.setdefault(
                    route["source_kind"], route["todo_id_prefix"]
                )
                if previous_prefix != route["todo_id_prefix"]:
                    raise ValueError("Correction Todo ID prefix mapping is ambiguous")
            try:
                workflow_items = workflow_feedback_todo_items(
                    Path(str(getattr(workflow_entry, "path", workflow_entry))),
                    target_step=state.current_step,
                    source_by_kind=source_by_kind,
                    id_prefix_by_kind=id_prefix_by_kind,
                    source_identities=(
                        delivered if delivered is not None else human_task_identities
                    ),
                )
            except TodoContractError as exc:
                raise ValueError(f"Correction Todo provenance is invalid: {exc}") from exc
            if workflow_items:
                resolved = dict(artifacts)
                resolved[causal_artifact] = TodoSourceArtifact(
                    artifact=selected_route["artifact"],
                    source=workflow_items[0].source,
                    path=Path(str(getattr(workflow_entry, "path", workflow_entry))),
                    version=getattr(workflow_entry, "version", None),
                    items=workflow_items,
                )
                return resolved

        if selected is not None and getattr(selected, "updated_by", from_step) != from_step:
            raise ValueError("Correction Todo artifact ownership conflicts with provenance")
        if selected is None and isinstance(direct_artifact, str):
            raise ValueError("Correction Todo provenance is missing")
        if selected is None and from_step not in {None, state.current_step}:
            raise ValueError("Correction Todo provenance is unsupported")
        if selected is None:
            return artifacts
        producer_config = steps.get(from_step, {}) if from_step else {}
        backward_targets = set(
            producer_config.get("allowed_goto", [])
            if isinstance(producer_config, Mapping)
            else []
        )
        if isinstance(producer_config, Mapping):
            for binding in producer_config.get("human_tasks", ()) or ():
                if isinstance(binding, Mapping):
                    backward_targets.update(
                        target
                        for target in [
                            *(binding.get("outcomes", {}) or {}).values(),
                            *(binding.get("allowed_targets", ()) or ()),
                        ]
                        if target != "_done"
                    )
        if (
            current_behavior is None
            or (
                current_behavior.feedback_target != state.current_step
                and state.current_step not in backward_targets
            )
        ):
            # A normal forward handoff may carry the producer's document, but it
            # is not a correction source and must not activate correction mode.
            return artifacts
        resolved = dict(artifacts)
        resolved[causal_artifact] = selected
        return resolved

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
        contract: SkillWorkflowDeclaration,
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
        contract: SkillWorkflowDeclaration,
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
        contract: SkillWorkflowDeclaration,
    ) -> None:
        """Expose only the declared template catalog or resolved selected file."""
        if contract.output_templates is None:
            return
        context["template_catalog"] = contract.output_templates.catalog
        template_file = self._resolved_template_file(step_name, step_def, skill_name, contract)
        if template_file is not None:
            context["template_file"] = template_file

    def _publish_workspace_artifact(
        self,
        *,
        step_name: str,
        step_def: Dict[str, Any],
        output_file: Path,
        blackboard_state: BlackboardState,
        updated_at: Optional[str] = None,
    ) -> tuple[str, dict[str, Any]] | None:
        repo = Path(getattr(self.git_ops, "repo_path", Path.cwd())).resolve()
        with workspace_execution_lock(repo):
            return self._publish_workspace_artifact_under_lock(
                step_name=step_name,
                step_def=step_def,
                output_file=output_file,
                blackboard_state=blackboard_state,
                updated_at=updated_at,
            )

    def _publish_workspace_artifact_under_lock(
        self,
        *,
        step_name: str,
        step_def: Dict[str, Any],
        output_file: Path,
        blackboard_state: BlackboardState,
        updated_at: Optional[str] = None,
    ) -> tuple[str, dict[str, Any]] | None:
        """Publish the one declared workspace companion from current Git state."""
        workspace_name = step_def.get("workspace_artifact")
        if not isinstance(workspace_name, str) or not workspace_name.strip():
            return None
        summary_name = str(step_def.get("output_artifact", step_name))
        if workspace_name == summary_name:
            raise ValueError("workspace artifact must be distinct from the summary artifact")
        repo = Path(getattr(self.git_ops, "repo_path", Path.cwd())).resolve()
        base_ref = self._get_issue_config_value(self.issue_dir / "issue.yaml", "base_branch")
        if not base_ref:
            base_ref = self.git_ops.get_default_base_branch()
        head_sha = self.git_ops.run_git("rev-parse", "HEAD")
        try:
            # The configured base can move independently after this feature branch
            # starts.  A workspace snapshot must use an actual ancestor of HEAD;
            # derive that anchor locally without fetching, merging, or rebasing.
            base_sha = self.git_ops.run_git("merge-base", str(base_ref), head_sha)
        except GitError as exc:
            raise ValueError(
                f"workspace artifact {workspace_name!r} has no common ancestor between "
                f"configured base {base_ref!r} and HEAD"
            ) from exc
        try:
            candidate = build_workspace_artifact(
                repo=repo,
                name=workspace_name,
                version=1,
                base_sha=base_sha,
                head_sha=head_sha,
                updated_at=updated_at or datetime.now(timezone.utc).isoformat(),
                producer_step=step_name,
            )
        except WorkspaceArtifactError as exc:
            raise ValueError(f"workspace artifact {workspace_name!r} is invalid: {exc}") from exc
        previous = blackboard_state.artifacts.get(workspace_name)
        previous_workspace: Optional[WorkspaceArtifact] = None
        if previous is not None:
            try:
                previous_workspace = WorkspaceArtifact.from_dict(
                    json.loads(Path(previous.path).read_text(encoding="utf-8"))
                )
            except (OSError, UnicodeError, json.JSONDecodeError, WorkspaceArtifactError):
                previous_workspace = None
        same_snapshot = previous_workspace is not None and (
            previous_workspace.name == candidate.name
            and previous_workspace.repository == candidate.repository
            and previous_workspace.base_sha == candidate.base_sha
            and previous_workspace.head_sha == candidate.head_sha
            and previous_workspace.changed_files == candidate.changed_files
        )
        version = previous.version if same_snapshot and previous is not None else (
            previous.version + 1 if previous else 1
        )
        workspace = WorkspaceArtifact(
            name=candidate.name,
            version=version,
            repository=candidate.repository,
            base_sha=candidate.base_sha,
            head_sha=candidate.head_sha,
            changed_files=candidate.changed_files,
            schema_version=candidate.schema_version,
            updated_at=candidate.updated_at,
            producer_step=candidate.producer_step,
        )
        workspace_path = output_file.parent / "workspace.json"
        temporary = workspace_path.with_name(f".{workspace_path.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(workspace.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temporary, workspace_path)
        return str(workspace_path), {
            "kind": "workspace",
            "name": workspace.name,
            "version": workspace.version,
            "base_sha": workspace.base_sha,
            "head_sha": workspace.head_sha,
            "updated_by": step_name,
            "updated_at": workspace.updated_at,
            "producer_step": workspace.producer_step,
        }

    def _write_artifact_record(
        self,
        *,
        blackboard_state: BlackboardState,
        output_key: str,
        output_path: str,
        updated_by: str,
    ) -> ArtifactEntry:
        previous = blackboard_state.artifacts.get(output_key)
        version = previous.version + 1 if previous else 1
        kind = ArtifactKind.DOCUMENT
        output_bytes = Path(output_path).read_bytes()
        todo_identities: Optional[dict[str, str]] = None
        todo_work_identities: Optional[dict[str, str]] = None
        todo_identity_baseline: Optional[dict[str, Any]] = None
        content = output_bytes.decode("utf-8")
        plan_document = None
        todo_items: tuple[Any, ...] | None = None
        stripped_lines = [line.strip() for line in content.splitlines()]
        first_nonblank = next((line for line in stripped_lines if line), "")
        if first_nonblank.startswith("<!-- plan-stage:"):
            try:
                plan_document = parse_plan_todo_document(content)
            except TodoContractError as exc:
                raise ValueError(
                    f"artifact {output_key!r} has an invalid Todo List: {exc}"
                ) from exc
            todo_items = plan_document.items
        elif "## Todo List" in content:
            try:
                todo_items = parse_todo_list(content)
            except TodoContractError as exc:
                raise ValueError(
                    f"artifact {output_key!r} has an invalid Todo List: {exc}"
                ) from exc
        if (
            plan_document is not None
            and plan_document.kind is PlanTodoDocumentKind.PROVISIONAL_ALIGNMENT
        ):
            if previous is None:
                todo_identity_baseline = {
                    "schema_version": _TODO_IDENTITY_BASELINE_SCHEMA_VERSION,
                    "artifact": None,
                }
            else:
                previous_authority, _previous_items = self._resolve_plan_todo_authority(
                    output_key, previous, allow_missing_legacy_record=True
                )
                if previous.todo_identity_baseline is not None:
                    todo_identity_baseline = _normalize_todo_identity_baseline(
                        previous.todo_identity_baseline, artifact_name=output_key
                    )
                elif previous_authority is not None:
                    todo_identity_baseline = _todo_identity_baseline_reference(
                        previous_authority
                    )
                else:
                    todo_identity_baseline = {
                        "schema_version": _TODO_IDENTITY_BASELINE_SCHEMA_VERSION,
                        "artifact": None,
                    }
        elif todo_items is not None:
            continuity_proofs = parse_todo_identity_continuity(content)
            plan_items = [item for item in todo_items if item.source == "plan"]
            if plan_items:
                todo_identities = {
                    item.item_id: hashlib.sha256(
                        "\x1f".join(
                            (item.source, item.item_id, " ".join(item.work.split()))
                        ).encode("utf-8")
                    ).hexdigest()
                    for item in plan_items
                }
                todo_work_identities = {
                    _plan_work_identity(item): item.item_id for item in plan_items
                }
                previous_items: tuple[Any, ...] = ()
                previous_work_identities: dict[str, str] = {}
                if previous is not None:
                    previous_authority, previous_items = self._resolve_plan_todo_authority(
                        output_key, previous, allow_missing_legacy_record=True
                    )
                    if previous_authority is not None:
                        previous_work_identities = dict(
                            previous_authority.todo_work_identities or {}
                        )
                if previous_work_identities:
                    # PLAN-NNN is the durable authoring contract.  Unchanged
                    # work may be reordered, while a changed retained item must
                    # carry an explicit proof against its persisted work hash.
                    previous_by_id = {
                        item.item_id: item
                        for item in previous_items
                        if item.source == "plan"
                    }
                    for item in plan_items:
                        prior_id = previous_work_identities.get(_plan_work_identity(item))
                        if prior_id is not None and prior_id != item.item_id:
                            raise ValueError(
                                f"plan Todo identity {prior_id!r} was moved to "
                                f"{item.item_id!r}; retain the existing ID"
                            )
                        prior_item = previous_by_id.get(item.item_id)
                        if prior_item is not None and (
                            _plan_work_identity(prior_item) != _plan_work_identity(item)
                        ):
                            if continuity_proofs.get(item.item_id) != _plan_work_identity(
                                prior_item
                            ):
                                raise ValueError(
                                    f"plan Todo identity {item.item_id!r} changed work "
                                    "without a continuity proof; retain the existing ID "
                                    "or declare the prior work fingerprint"
                                )
        artifact = ArtifactEntry(
            name=output_key,
            kind=kind,
            version=version,
            updated_by=updated_by,
            path=output_path,
            content_sha256=hashlib.sha256(output_bytes).hexdigest(),
            todo_identities=todo_identities,
            todo_work_identities=todo_work_identities,
            todo_identity_baseline=todo_identity_baseline,
        )
        artifact_path = self._get_iteration_dir(self.iteration) / "artifact.json"
        temporary = artifact_path.with_name(f".{artifact_path.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(artifact.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, artifact_path)
        return artifact

    def _validate_outbound_causal_todo(
        self,
        *,
        step_name: str,
        step_def: Dict[str, Any],
        blackboard_state: BlackboardState,
        output_file: Path,
        response: str,
        status_code: Optional[PhaseStatusCode],
        auto_continue: bool,
    ) -> tuple[bool, str, bool]:
        """Validate a producer artifact before a declared causal Todo handoff."""
        if step_def.get("hybrid_portion"):
            return True, "", False

        target: Optional[str] = None
        baton_path = self.issue_dir / "next_step.txt"
        if self._agent_wrote_baton(step_name, step_def):
            payload = json.loads(baton_path.read_text(encoding="utf-8"))
            handoff = HandoffContract.from_dict_with_current_step(
                payload,
                current_step=step_name,
            )
            if handoff.to_owner == HandoffOwner.AGENT:
                target = handoff.to_step
        elif status_code is not None:
            pauses_for_user = not auto_continue and status_code in {
                PhaseStatusCode.READY_FOR_REVIEW,
                PhaseStatusCode.CONFIRM_OUTPUT,
                PhaseStatusCode.ALIGNMENT_CHECKPOINT,
                PhaseStatusCode.NEED_CLARIFICATION,
                PhaseStatusCode.NEED_PERMISSION,
            }
            no_changes_pauses = status_code == PhaseStatusCode.NO_CHANGES_NEEDED and (
                self.interactive
                or self._declared_human_task_id(step_def, "no_changes_needed") is not None
            )
            if status_code == PhaseStatusCode.NO_CHANGES_NEEDED:
                transitions = step_def.get("on", {})
                candidate = (
                    transitions.get("no_changes_needed")
                    if isinstance(transitions, dict)
                    else None
                )
                if (
                    not no_changes_pauses
                    and candidate != "user"
                    and candidate in self.playbook.get("steps", {})
                ):
                    target = str(candidate)
            elif not pauses_for_user:
                target = self._resolve_next_step_for_status(
                    step_name=step_name,
                    step_def=step_def,
                    response=response,
                    status_code=status_code,
                )

        steps = self.playbook.get("steps", {})
        target_def = steps.get(target) if isinstance(steps, Mapping) else None
        if not isinstance(target_def, dict):
            return True, "", False

        output_key = str(step_def.get("output_artifact", step_name))
        input_artifacts = target_def.get("input_artifacts")
        if input_artifacts is not None and output_key not in input_artifacts:
            return True, "", False

        producer_behavior = resolve_step_behavior(self.playbook, step_name)
        route = (producer_behavior.feedback_routes or {}).get(target)
        if route is not None:
            try:
                routed_items = parse_todo_list(
                    output_file.read_text(encoding="utf-8"),
                    expected_source=str(route.todo_source),
                )
                prefix = f"{route.todo_id_prefix}-"
                if any(not item.item_id.startswith(prefix) for item in routed_items):
                    raise TodoContractError("Todo item ID does not match the declared route prefix")
            except (OSError, UnicodeError, TodoContractError) as exc:
                return (
                    False,
                    f"The correction Todo source for {step_name!r}->{target!r} is invalid: {exc}. "
                    "Publish one complete canonical source before handoff.",
                    True,
                )

        target_iteration = self._get_next_iteration_number(
            target,
            self.issue_dir / target,
        )
        target_skill = self._resolve_skill_name(target_def, target_iteration)
        target_contract = self._get_skill_loader().get_workflow_declaration(target_skill)
        if target_contract.checklist is None:
            return True, "", route is not None

        prospective_artifacts = self._step_input_artifacts(target_def, blackboard_state)
        prospective_artifacts[output_key] = output_file
        causal_alias = next(
            (
                section.todo_projection.artifact
                for variant in target_contract.checklist.variants
                for section in variant.sections
                if section.todo_projection and section.todo_projection.causal
            ),
            None,
        )
        if causal_alias is None:
            return True, "", False
        prospective_artifacts[causal_alias] = output_file
        active_variant = select_checklist_variant(
            target_contract,
            step=target,
            iteration=target_iteration,
            artifacts=prospective_artifacts,
            feedback=True,
        )
        if not any(
            section.todo_projection and section.todo_projection.causal
            for section in active_variant.sections
        ):
            return True, "", False

        try:
            parse_todo_list(output_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, TodoContractError) as exc:
            return (
                False,
                f"The Todo List handed from {step_name!r} to {target!r} is invalid: {exc}. "
                "Use canonical rows with Source, Work, Closure, and Evidence fields, or "
                "the exact marker 'No actionable work.'.",
                True,
            )
        return True, "", True

    @staticmethod
    def _validate_produced_todo_output(output_file: Path) -> tuple[bool, str]:
        """Validate a produced Todo section before accepting its completion baton."""
        if not output_file.exists():
            return True, ""
        try:
            content = output_file.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            return False, f"The phase output is unreadable: {exc}."
        if "## Todo List" not in content:
            return True, ""
        try:
            parse_todo_list(content)
        except TodoContractError as exc:
            return (
                False,
                f"The phase output has an invalid Todo List: {exc}. "
                "Use canonical rows with Source, Work, Closure, and Evidence fields, or "
                "the exact marker 'No actionable work.'.",
            )
        return True, ""

    def _output_requires_contract_validation(
        self,
        *,
        step_name: str,
        status_code: Optional[PhaseStatusCode],
        baton_path: Path,
        hybrid_portion: bool,
    ) -> bool:
        try:
            payload = json.loads(baton_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("baton payload must be an object")
            contract = HandoffContract.from_dict_with_current_step(
                payload,
                current_step=step_name,
            )
            contract.validate(allowed_steps=list(self.playbook.get("steps", {})))
            valid_completion_baton = contract.from_step == step_name and (
                hybrid_portion or contract.to_step != step_name
            )
            if valid_completion_baton:
                return completion_requires_checklist(baton_intent=contract.intent.value)
        except (OSError, json.JSONDecodeError, ValueError, BatonRejected):
            pass
        return completion_requires_checklist(
            status_code=status_code.value if status_code is not None else None
        )

    def _validate_projected_todo_completion(self, checklist_path: Path) -> bool:
        """Re-resolve declared Todo sources before accepting phase completion."""
        passed, _detail = self._validate_projected_todo_completion_detail(checklist_path)
        return passed

    def _validate_projected_todo_completion_detail(
        self, checklist_path: Path
    ) -> tuple[bool, str]:
        """Return actionable projected Todo validation detail for agent retries."""
        if self.phase_name not in self.playbook.get("steps", {}):
            return True, ""
        skill_name = self._resolve_skill_name(
            self.playbook["steps"][self.phase_name], self.iteration
        )
        contract = self._get_skill_loader().get_workflow_declaration(skill_name)
        if contract.checklist is None:
            return True, ""
        state = BlackboardStore(self.issue_dir).load_or_create(self.phase_name)
        artifacts = self._step_input_artifacts(self.playbook["steps"][self.phase_name], state)
        causal_artifact = next(
            (
                section.todo_projection.artifact
                for candidate in contract.checklist.variants
                for section in candidate.sections
                if section.todo_projection and section.todo_projection.causal
            ),
            None,
        )
        if causal_artifact is not None:
            try:
                artifacts = self._add_causal_todo_artifact(
                    artifacts,
                    state,
                    playbook=self.playbook,
                    causal_artifact=causal_artifact,
                )
            except ValueError:
                return False, "The causal Todo artifact could not be resolved."
        feedback = bool(causal_artifact and artifacts.get(causal_artifact))
        variant = select_checklist_variant(
            contract,
            step=self.phase_name,
            iteration=self.iteration,
            artifacts=artifacts,
            feedback=feedback,
        )
        expected = []
        current_projections: list[dict[str, Any]] = []
        for section in variant.sections:
            if section.todo_projection is None:
                continue
            entry = artifacts.get(section.todo_projection.artifact)
            if entry is None:
                return (
                    False,
                    f"The projected Todo artifact {section.todo_projection.artifact!r} is missing.",
                )
            try:
                items = projection_todo_items(entry, expected_source=section.todo_projection.source)
                expected.extend(items)
                current_projections.append(
                    {
                        "declaration_artifact": section.todo_projection.artifact,
                        "artifact": str(
                            getattr(
                                entry,
                                "artifact",
                                getattr(entry, "name", section.todo_projection.artifact),
                            )
                        ),
                        "path": str(getattr(entry, "path", entry)),
                        "version": getattr(entry, "version", None),
                        "rows": [item.checklist_row() for item in items],
                    }
                )
            except (OSError, ValueError):
                return False, "The authoritative projected Todo source is invalid."
        output_path = self._get_versioned_file_path(self.phase_name, self.iteration, self.phase_dir)
        pinned = self._load_todo_projection_snapshot(output_path.parent)
        if pinned != current_projections:
            return False, "The projected Todo snapshot is stale."
        errors = validate_projected_todos(
            checklist_path,
            output_path,
            tuple(expected),
            repo_root=get_git_toplevel(),
        )
        if errors:
            templates = []
            for item in expected:
                templates.append(
                    f"### {item.item_id}\n"
                    "- Status: completed\n"
                    f"- Source fingerprint: `{item.fingerprint}`\n"
                    "- Files: `<repo-relative path>`\n"
                    "- Commit: `<full 40-character commit SHA>`\n"
                    "- Remaining work: None.\n"
                    "- Next action: Review."
                )
            return (
                False,
                "Projected Todo completion failed:\n- "
                + "\n- ".join(errors)
                + "\n\nUse exactly one '## Todo Progress' section with this canonical "
                "entry shape for each authoritative item:\n\n"
                + "\n\n".join(templates),
            )
        return True, ""

    def _validate_produced_packet_contracts(
        self, *, producer_step: str, artifact_name: str, output_file: Path
    ) -> None:
        """Deprecated compatibility hook; packet metadata never gates output."""

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
            contract.validate(allowed_steps=list(self.playbook.get("steps", {}).keys()))
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
        if capability_id == "cafe.browser.open":
            return {
                "capability": capability_id,
                "args": {"target_ref": "current_pr"},
                "effects": {
                    "browser_open": ["current_pr"],
                    "writes": [],
                    "network_destinations": [],
                },
                "credentials": [],
                "permissions": {},
            }

        if capability_id != CAPABILITY_PR_PUBLISH_ID:
            return {
                "capability": capability_id,
                "args": {},
                "effects": {
                    "browser_open": [],
                    "writes": [],
                    "network_destinations": [],
                },
                "credentials": [],
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
            "effects": {
                "browser_open": [],
                "network_destinations": ["github.com", "api.github.com"],
                "writes": [
                    self._repo_relative_path(output_file),
                    ".git",
                    self._repo_relative_path(self.issue_dir),
                ],
            },
            "credentials": ["gh"],
            "permissions": {
                "network": ["github.com", "api.github.com"],
                "writes": [
                    self._repo_relative_path(output_file),
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
