"""Read-only Driver-facing inspection of current HumanTask authority."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from cafe.core.human_tasks import HumanTaskQuestion
from cafe.core.questions_schema import parse_questions_xml, validate_questions_xml
from cafe.core.task_inbox import TaskInboxService

from ._store import load_contract
from .task_authority import decide_task_authority


def _read_json(path: Path, *, limit: int = 64 * 1024 * 1024) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise ValueError(f"unsafe or missing Driver inspection input: {path.name}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Driver inspection input must be an object: {path.name}")
    return value


def _sources(
    issue_dir: Path,
    contract: Mapping[str, Any],
    board: Mapping[str, Any],
    evidence: Mapping[str, Any] | None,
    active_step: str,
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
    root = issue_dir.parent.parent.parent.resolve()
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
        # The output of the paused phase has not been accepted yet.
        if entry.get("updated_by") == active_step:
            continue
        raw_path = entry.get("path")
        if not isinstance(raw_path, str):
            continue
        lexical_path = root / raw_path
        path = lexical_path.resolve()
        if not path.is_relative_to(issue_dir.resolve()):
            continue
        if any(candidate.is_symlink() for candidate in (lexical_path, *lexical_path.parents)):
            continue
        if path.is_file() and path.stat().st_size <= min(256 * 1024, remaining_bytes):
            content = path.read_bytes()
            if entry.get("content_sha256") != hashlib.sha256(content).hexdigest():
                continue
            sources[f"artifact:{name}"] = content.decode("utf-8")
            remaining_bytes -= len(content)
    return sources


def inspect_task_authority(
    issue_dir: Path,
    task_id: str,
    *,
    response: Mapping[str, Any] | None = None,
    evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Inspect one neutral task and return independent route, owner and evidence facts."""
    issue_dir = Path(issue_dir)
    detail = TaskInboxService(issue_dir.parent.parent).inspect_read_only(task_id)
    if detail.issue != issue_dir.name:
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
    if detail.expected_result.get("questions_from_xml") is True:
        questions_file = (
            issue_dir / detail.step / f"iteration_{detail.iteration:03d}" / "questions.xml"
        )
        if questions_file.is_file() and validate_questions_xml(questions_file):
            questions = tuple(
                HumanTaskQuestion(
                    id=item.id,
                    prompt=item.title,
                    options=tuple(item.options),
                    multiple=item.multi_select,
                )
                for item in parse_questions_xml(questions_file)
            )
    sources = _sources(issue_dir, contract, board, evidence, detail.step)
    result = decide_task_authority(
        task=task,
        contract=contract,
        current_task_id=task_id,
        response=response,
        evidence=evidence,
        confirmed_sources=sources,
        questions=questions,
    )
    if (
        handoff.get("from_step") != detail.step
        or handoff.get("intent") != detail.trigger
        or handoff.get("to_owner") != "user"
    ):
        result = {**result, "allowed": False, "evidence_reason": "stale_handoff"}
    return {
        **result,
        "task_id": detail.id,
        "task_name": detail.policy_id,
        "step": detail.step,
        "pause_status": handoff.get("status_code") or "unknown",
        "contract_sha256": digest,
        "sources_sha256": hashlib.sha256(
            json.dumps(sources, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest(),
    }
