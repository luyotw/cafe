"""Read-only Manager-facing inspection of current HumanTask authority."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from cafe.core.blackboard import BlackboardState
from cafe.core.human_task_records import HumanTaskRecordStore, HumanTaskStatus
from cafe.core.human_task_snapshot import durable_task_matches_current_handoff, read_iteration_file
from cafe.core.human_tasks import HumanTaskPolicy, HumanTaskQuestion
from cafe.core.questions_schema import parse_validated_questions_xml
from cafe.core.task_inbox import TaskInboxService
from cafe.playbooks.loader import PlaybookLoader
from cafe.skills.loader import SkillLoader
from cafe.utils.issue_paths import issue_name_from_dir, issue_project_root

from ._store import load_contract
from .clarification_context import clarification_context
from .delivery_comparison import comparison_packet_from_contract
from .task_authority import decide_task_authority


def _read_json(path: Path, *, limit: int = 64 * 1024 * 1024) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise ValueError(f"unsafe or missing Manager inspection input: {path.name}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Manager inspection input must be an object: {path.name}")
    return value


def _iteration_questions(
    issue_dir: Path, iteration_path: Path
) -> tuple[tuple[HumanTaskQuestion, ...] | None, str | None]:
    content = read_iteration_file(issue_dir, iteration_path / "questions.xml")
    if content is None or len(content) > 256 * 1024:
        return None, None
    try:
        questions = tuple(
            HumanTaskQuestion(
                id=item.id, prompt=item.title, options=tuple(item.options),
                multiple=item.multi_select,
            )
            for item in parse_validated_questions_xml(content)
        )
    except ValueError:
        return None, None
    return questions, hashlib.sha256(content).hexdigest()


def _sources(
    issue_dir: Path,
    contract: Mapping[str, Any],
    board: Mapping[str, Any],
    evidence: Mapping[str, Any] | None,
    active_step: str,
    active_iteration: int,
    include_current_output: bool = False,
) -> dict[str, str]:
    sources = {
        "contract": json.dumps(contract["delivery_contract"], ensure_ascii=False, sort_keys=True)
    }
    decisions = board.get("decisions")
    if isinstance(decisions, list):
        for index, decision in enumerate(decisions):
            if isinstance(decision, Mapping) and decision.get("made_by") == "user":
                sources[f"decision:{index}"] = json.dumps(
                    {"decision": decision.get("decision"), "rationale": decision.get("rationale")},
                    ensure_ascii=False,
                    sort_keys=True,
                )
    artifacts = board.get("artifacts")
    if not isinstance(artifacts, Mapping):
        artifacts = {}
    root = issue_project_root(issue_dir).resolve()
    remaining_bytes = 2 * 1024 * 1024
    repository_sources = (
        evidence.get("repository_sources", []) if isinstance(evidence, Mapping) else []
    )
    if isinstance(repository_sources, list) and len(repository_sources) <= 8:
        for raw_path in repository_sources:
            if not isinstance(raw_path, str) or not raw_path or Path(raw_path).is_absolute():
                continue
            if Path(raw_path).parts[:1] == (".cafe",):
                continue
            lexical_path = root / raw_path
            path = lexical_path.resolve()
            if not path.is_relative_to(root):
                continue
            if any(candidate.is_symlink() for candidate in (lexical_path, *lexical_path.parents)):
                continue
            if path.is_file() and path.stat().st_size <= min(256 * 1024, remaining_bytes):
                sources[f"repo:{raw_path}"] = path.read_text(encoding="utf-8")
                remaining_bytes -= path.stat().st_size
    for name, entry in list(artifacts.items())[:32]:
        if not isinstance(name, str) or not isinstance(entry, Mapping):
            continue
        current_output = entry.get("updated_by") == active_step
        # A pending confirmation may review its current output, but that output
        # never becomes an accepted upstream artifact citation.
        if current_output and not include_current_output:
            continue
        raw_path = entry.get("path")
        if not isinstance(raw_path, str):
            continue
        lexical_path = root / raw_path
        path = lexical_path.resolve()
        if not path.is_relative_to(issue_dir.resolve()):
            continue
        if (
            current_output
            and path
            != (
                issue_dir / active_step / f"iteration_{active_iteration:03d}" / "output.md"
            ).resolve()
        ):
            continue
        if any(candidate.is_symlink() for candidate in (lexical_path, *lexical_path.parents)):
            continue
        if path.is_file() and path.stat().st_size <= min(256 * 1024, remaining_bytes):
            content = path.read_bytes()
            if (
                not current_output
                and entry.get("content_sha256") != hashlib.sha256(content).hexdigest()
            ):
                continue
            source_kind = "current_output" if current_output else "artifact"
            sources[f"{source_kind}:{name}"] = content.decode("utf-8")
            remaining_bytes -= len(content)
    return sources


def inspect_task_authority(
    issue_dir: Path,
    task_id: str,
    *,
    response: Mapping[str, Any] | None = None,
    evidence: Mapping[str, Any] | None = None,
    include_clarification_context: bool = False,
) -> dict[str, Any]:
    """Inspect one neutral task and return independent route, owner and evidence facts."""
    issue_dir = Path(issue_dir)
    detail = TaskInboxService(issue_project_root(issue_dir) / ".cafe").inspect_read_only(task_id)
    if detail.issue != issue_name_from_dir(issue_dir):
        raise ValueError("task belongs to a different issue")
    contract, digest = load_contract(
        issue_dir,
        issue_name=detail.issue,
        workflow_id=detail.workflow_id,
        allow_legacy_upgrade=True,
    )
    board = _read_json(issue_dir / "blackboard.json")
    if board.get("workflow_id") != detail.workflow_id:
        raise ValueError("workflow identity changed during task inspection")
    handoff = board.get("handoff_contract")
    handoff = handoff if isinstance(handoff, Mapping) else {}
    task = detail.to_dict()
    questions = None
    questions_sha256 = None
    iteration_path = Path(detail.step) / f"iteration_{detail.iteration:03d}"
    if detail.expected_result.get("questions_from_xml") is True:
        questions, questions_sha256 = _iteration_questions(issue_dir, iteration_path)
    sources = _sources(
        issue_dir,
        contract,
        board,
        evidence,
        detail.step,
        detail.iteration,
        include_current_output=detail.trigger == "confirm_output",
    )
    trusted_comparison = None
    if (
        detail.trigger == "confirm_output"
        and isinstance(evidence, Mapping)
        and isinstance(evidence.get("delivery_comparison"), Mapping)
    ):
        project_root = issue_project_root(issue_dir)
        try:
            model = PlaybookLoader(project_root=project_root).load_model(board["playbook_id"]).model
            trusted_comparison = comparison_packet_from_contract(
                issue_dir=issue_dir,
                issue_name=detail.issue,
                workflow_id=detail.workflow_id,
                contract=contract,
                contract_sha256=digest,
                model=model,
                skill_loader=SkillLoader(project_root=project_root),
                boundary={
                    "step": detail.step,
                    "task_id": detail.id,
                    "iteration": detail.iteration,
                    "intent": detail.trigger,
                    "owner": "user",
                    "active": True,
                },
                artifacts={
                    key.split(":", 1)[1]: text
                    for key, text in sources.items()
                    if key.startswith(("artifact:", "current_output:"))
                },
            )
        except (OSError, ValueError, KeyError, TypeError):
            pass
    result = decide_task_authority(
        task=task,
        contract=contract,
        current_task_id=task_id,
        response=response,
        evidence=evidence,
        confirmed_sources=sources,
        questions=questions,
        trusted_comparison=trusted_comparison,
    )
    if (
        handoff.get("from_step") != detail.step
        or handoff.get("intent") != detail.trigger
        or handoff.get("to_owner") != "user"
    ):
        result = {**result, "allowed": False, "evidence_reason": "stale_handoff"}
    decision_inputs = {
        "sources": sources,
        "task": task,
        "handoff": handoff,
        "questions_sha256": questions_sha256,
    }
    # This digest binds inspection inputs, not later concurrent edits during completion.
    facts = {
        **result,
        "task_id": detail.id,
        "task_name": detail.policy_id,
        "step": detail.step,
        "pause_status": handoff.get("status_code") or "unknown",
        "contract_sha256": digest,
        "sources_sha256": hashlib.sha256(
            json.dumps(decision_inputs, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest(),
    }
    if include_clarification_context:
        facts["clarification_context"] = _clarification_preview(
            issue_dir, task_id, iteration_path, board, facts, contract, sources,
            detail.expected_result, questions,
        )
    return facts


def _clarification_preview(
    issue_dir: Path,
    task_id: str,
    iteration_path: Path,
    board: dict[str, Any],
    facts: Mapping[str, Any],
    contract: Mapping[str, Any],
    confirmed_sources: Mapping[str, str],
    expected_result: Mapping[str, Any],
    questions: tuple[HumanTaskQuestion, ...] | None,
) -> dict[str, Any]:
    if (
        facts["route_status"] != "need_clarification"
        or facts["resolution_owner"] != "manager_confirmable"
        or facts["evidence_reason"] != "evidence_unevaluated"
    ):
        return {"status": "unavailable", "reason": "clarification_not_eligible"}
    state = BlackboardState.from_dict(board, initial_step="")
    active = [
        task.id
        for task in HumanTaskRecordStore(issue_dir).tasks()
        if task.status is HumanTaskStatus.PENDING
        and task.workflow_id == state.workflow_id
        and durable_task_matches_current_handoff(task, state, require_exact_handoff=True)
    ]
    if active != [task_id]:
        return {"status": "unavailable", "reason": "clarification_not_current"}
    try:
        policy = HumanTaskPolicy.model_validate(expected_result)
    except ValueError:
        return {"status": "unavailable", "reason": "clarification_questions_unavailable"}
    # Feedback tasks also use questions.xml as the worker's explanation. Only
    # declared XML answer contracts above participate in the completion digest.
    if policy.input_schema == "feedback":
        questions, _ = _iteration_questions(issue_dir, iteration_path)
    elif policy.input_schema != "answers" or (policy.questions and policy.questions_from_xml):
        return {"status": "unavailable", "reason": "clarification_question_source_ambiguous"}
    elif not policy.questions_from_xml:
        questions = policy.questions
    if not questions or len(questions) != len({question.id for question in questions}):
        return {"status": "unavailable", "reason": "clarification_questions_unavailable"}
    sources = dict(confirmed_sources)
    root = issue_project_root(issue_dir).resolve()
    paths = {
        source: (root / state.artifacts[source.removeprefix("artifact:")].path)
        .resolve().relative_to(root).as_posix()
        for source in sources
        if source.startswith("artifact:")
    }
    checkpoint = read_iteration_file(issue_dir, iteration_path / "output.md")
    if checkpoint is not None and len(checkpoint) <= 256 * 1024:
        try:
            sources["worker_checkpoint"] = checkpoint.decode("utf-8")
        except UnicodeError:
            pass
        else:
            paths["worker_checkpoint"] = (
                issue_dir.resolve() / iteration_path / "output.md"
            ).relative_to(root).as_posix()
    return clarification_context(questions, contract["delivery_contract"], sources, paths)
