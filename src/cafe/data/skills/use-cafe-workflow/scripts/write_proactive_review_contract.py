#!/usr/bin/env python3
"""Persist one user-confirmed proactive driver-review contract per issue."""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any


def _reexec_with_cafe_python() -> None:
    """Restart this writer with the interpreter that owns the cafe command."""
    if os.environ.get("CAFE_PROACTIVE_REVIEW_WRITER_REEXEC") == "1":
        raise RuntimeError("cafe's Python environment cannot import writer dependencies")
    cafe_command = shutil.which("cafe")
    if cafe_command is None:
        raise RuntimeError("cafe command not found; cannot load writer dependencies")
    first_line = Path(cafe_command).read_text(encoding="utf-8").splitlines()[0]
    if not first_line.startswith("#!"):
        raise RuntimeError(f"cafe command has no interpreter shebang: {cafe_command}")
    interpreter = shlex.split(first_line[2:].strip())
    if not interpreter:
        raise RuntimeError(f"cafe command has an empty interpreter shebang: {cafe_command}")
    environment = dict(os.environ)
    environment["CAFE_PROACTIVE_REVIEW_WRITER_REEXEC"] = "1"
    os.execvpe(
        interpreter[0],
        [*interpreter, str(Path(__file__).resolve()), *sys.argv[1:]],
        environment,
    )


try:
    import yaml

    from cafe.playbooks.loader import PlaybookLoader
except ModuleNotFoundError:
    _reexec_with_cafe_python()
    raise

_SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(_SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIRECTORY))

from format_kickoff_contract import _parse_proactive_review_decisions  # noqa: E402


def _agent_phase_names(*, project_root: Path, playbook_id: str) -> tuple[str, ...]:
    model = PlaybookLoader(project_root=project_root).load_model(playbook_id).model
    return tuple(
        step_name
        for step_name, step in model.steps.items()
        if step.assignee_type in {"agent", "hybrid"}
    )


def _is_within(*, candidate: Path, parent: Path) -> bool:
    try:
        candidate.relative_to(parent)
    except ValueError:
        return False
    return True


def _ensure_contract_target_containment(*, project_root: Path, target: Path) -> None:
    resolved_project_root = project_root.resolve()
    resolved_issue_root = (resolved_project_root / ".cafe" / "issues").resolve()
    resolved_target = target.resolve()
    issue_root_is_contained = _is_within(
        candidate=resolved_issue_root,
        parent=resolved_project_root,
    )
    target_is_contained = _is_within(
        candidate=resolved_target,
        parent=resolved_issue_root,
    )
    if not issue_root_is_contained or not target_is_contained:
        raise ValueError("proactive review contract target escapes the issue root")


def _contract_target(*, project_root: Path, issue_name: str) -> Path:
    normalized_name = issue_name.strip()
    candidate = Path(normalized_name)
    if (
        not normalized_name
        or candidate.is_absolute()
        or len(candidate.parts) != 1
        or normalized_name in {".", ".."}
    ):
        raise ValueError("issue name must be a single relative directory name")
    target = (
        project_root
        / ".cafe"
        / "issues"
        / normalized_name
        / "driver"
        / "proactive_review.yaml"
    )
    _ensure_contract_target_containment(project_root=project_root, target=target)
    return target


def _candidate_document(
    *,
    project_root: Path,
    playbook_id: str,
    decision_values: list[str],
    confirmed_by: str,
    confirmed_at: str,
) -> dict[str, Any]:
    confirmed_by, confirmed_at = confirmed_by.strip(), confirmed_at.strip()
    if not confirmed_by or not confirmed_at:
        raise ValueError("confirmed_by and confirmed_at must be non-empty")
    agent_phase_names = _agent_phase_names(
        project_root=project_root,
        playbook_id=playbook_id,
    )
    decisions = _parse_proactive_review_decisions(
        decision_values,
        phase_names=set(agent_phase_names),
        agent_phase_names=agent_phase_names,
    )
    return {
        "version": 1,
        "playbook_id": playbook_id,
        "phase_decisions": decisions,
        "confirmed_by": confirmed_by,
        "confirmed_at": confirmed_at,
    }


def _validate_existing_document(*, document: Any, project_root: Path) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise ValueError("proactive review contract must be a mapping")
    expected_fields = {
        "version",
        "playbook_id",
        "phase_decisions",
        "confirmed_by",
        "confirmed_at",
    }
    if set(document) != expected_fields or document.get("version") != 1:
        raise ValueError("proactive review contract has an unsupported schema")
    playbook_id = document.get("playbook_id")
    decisions = document.get("phase_decisions")
    confirmed_by = document.get("confirmed_by")
    confirmed_at = document.get("confirmed_at")
    if not isinstance(playbook_id, str) or not isinstance(decisions, list):
        raise ValueError("proactive review contract has invalid decision fields")
    if not isinstance(confirmed_by, str) or not isinstance(confirmed_at, str):
        raise ValueError("proactive review contract has invalid confirmation metadata")
    decision_values: list[str] = []
    for decision in decisions:
        if not isinstance(decision, dict) or set(decision) != {
            "phase",
            "decision",
            "rationale",
        }:
            raise ValueError("proactive review contract contains an invalid phase decision schema")
        phase = decision.get("phase")
        action = decision.get("decision")
        rationale = decision.get("rationale")
        if not all(isinstance(value, str) for value in (phase, action, rationale)):
            raise ValueError("proactive review contract contains an invalid phase decision")
        decision_values.append(f"{phase}={action}={rationale}")
    return _candidate_document(
        project_root=project_root,
        playbook_id=playbook_id,
        decision_values=decision_values,
        confirmed_by=confirmed_by,
        confirmed_at=confirmed_at,
    )


def _semantic_contract(document: dict[str, Any]) -> tuple[Any, ...]:
    return (
        document["playbook_id"],
        tuple(
            (item["phase"], item["decision"], item["rationale"])
            for item in document["phase_decisions"]
        ),
    )


def _read_existing_contract(*, target: Path, project_root: Path) -> dict[str, Any]:
    raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    return _validate_existing_document(document=raw, project_root=project_root)


def _replace_document(*, target: Path, document: dict[str, Any], project_root: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    _ensure_contract_target_containment(project_root=project_root, target=target)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
        delete=False,
    ) as temporary:
        temporary_path = Path(temporary.name)
        yaml.safe_dump(document, temporary, sort_keys=False, allow_unicode=True)
    try:
        written = _read_existing_contract(target=temporary_path, project_root=project_root)
        if written != document:
            raise ValueError("proactive review contract did not validate after serialization")
        os.replace(temporary_path, target)
    finally:
        temporary_path.unlink(missing_ok=True)


def write_proactive_review_contract(
    *,
    project_root: Path,
    issue_name: str,
    playbook_id: str,
    decision_values: list[str],
    confirmed_by: str,
    confirmed_at: str,
    replacement_confirmed: bool,
) -> str:
    """Create, reuse, or explicitly replace the one confirmed contract."""
    target = _contract_target(project_root=project_root, issue_name=issue_name)
    candidate = _candidate_document(
        project_root=project_root,
        playbook_id=playbook_id,
        decision_values=decision_values,
        confirmed_by=confirmed_by,
        confirmed_at=confirmed_at,
    )
    if not target.exists():
        _replace_document(target=target, document=candidate, project_root=project_root)
        return "created"
    try:
        existing = _read_existing_contract(target=target, project_root=project_root)
    except (FileNotFoundError, ValueError, yaml.YAMLError):
        if not replacement_confirmed:
            raise
        _replace_document(target=target, document=candidate, project_root=project_root)
        return "replaced"
    if _semantic_contract(existing) == _semantic_contract(candidate):
        return "reused"
    if not replacement_confirmed:
        raise ValueError(
            "changed proactive review contract requires complete replacement confirmation"
        )
    _replace_document(target=target, document=candidate, project_root=project_root)
    return "replaced"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Persist one confirmed proactive driver-review contract."
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--issue-name", required=True)
    parser.add_argument("--playbook-id", required=True)
    parser.add_argument(
        "--proactive-review-decision",
        action="append",
        default=[],
        metavar="STEP=required|not_required=RATIONALE",
    )
    parser.add_argument("--confirmed-by", required=True)
    parser.add_argument("--confirmed-at", required=True)
    parser.add_argument("--replacement-confirmed", action="store_true")
    args = parser.parse_args()
    project_root = args.project_root.resolve()
    try:
        result = write_proactive_review_contract(
            project_root=project_root,
            issue_name=args.issue_name,
            playbook_id=args.playbook_id,
            decision_values=args.proactive_review_decision,
            confirmed_by=args.confirmed_by,
            confirmed_at=args.confirmed_at,
            replacement_confirmed=args.replacement_confirmed,
        )
    except (OSError, ValueError, yaml.YAMLError) as exc:
        parser.error(str(exc))
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
