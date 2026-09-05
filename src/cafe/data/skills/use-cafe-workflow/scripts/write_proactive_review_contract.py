#!/usr/bin/env python3
"""Persist one user-confirmed proactive driver-review contract per issue."""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


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


def _directory_open_flags() -> int:
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise OSError("secure proactive review contract publication is unavailable")
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def _open_or_create_directory(*, parent_fd: int, name: str) -> int:
    flags = _directory_open_flags()
    try:
        return os.open(name, flags, dir_fd=parent_fd)
    except FileNotFoundError:
        try:
            os.mkdir(name, dir_fd=parent_fd)
        except FileExistsError:
            pass
        return os.open(name, flags, dir_fd=parent_fd)


@contextmanager
def _open_contract_directory(*, project_root: Path, issue_name: str) -> Iterator[int]:
    directory_fd = os.open(project_root.resolve(), _directory_open_flags())
    try:
        for component in (".cafe", "issues", issue_name, "driver"):
            child_fd = _open_or_create_directory(parent_fd=directory_fd, name=component)
            os.close(directory_fd)
            directory_fd = child_fd
        yield directory_fd
    finally:
        os.close(directory_fd)


def _open_temporary_contract_file(*, directory_fd: int, target_name: str) -> tuple[int, str]:
    for _ in range(100):
        temporary_name = f".{target_name}.{uuid.uuid4().hex}.tmp"
        try:
            descriptor = os.open(
                temporary_name,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=directory_fd,
            )
        except FileExistsError:
            continue
        return descriptor, temporary_name
    raise FileExistsError("could not reserve a temporary proactive review contract")


def _normalized_issue_name(issue_name: str) -> str:
    normalized_name = issue_name.strip()
    candidate = Path(normalized_name)
    if (
        not normalized_name
        or candidate.is_absolute()
        or len(candidate.parts) != 1
        or normalized_name in {".", ".."}
    ):
        raise ValueError("issue name must be a single relative directory name")
    return normalized_name


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


def _read_existing_contract(*, directory_fd: int, project_root: Path) -> dict[str, Any]:
    descriptor = os.open(
        "proactive_review.yaml",
        os.O_RDONLY | os.O_NOFOLLOW,
        dir_fd=directory_fd,
    )
    with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    return _validate_existing_document(document=raw, project_root=project_root)


def _contract_exists(*, directory_fd: int) -> bool:
    try:
        os.stat("proactive_review.yaml", dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def _replace_document(*, directory_fd: int, document: dict[str, Any], project_root: Path) -> None:
    descriptor, temporary_name = _open_temporary_contract_file(
        directory_fd=directory_fd,
        target_name="proactive_review.yaml",
    )
    try:
        with os.fdopen(descriptor, "w+", encoding="utf-8") as temporary:
            yaml.safe_dump(document, temporary, sort_keys=False, allow_unicode=True)
            temporary.flush()
            temporary.seek(0)
            written = _validate_existing_document(
                document=yaml.safe_load(temporary),
                project_root=project_root,
            )
        if written != document:
            raise ValueError("proactive review contract did not validate after serialization")
        os.replace(
            temporary_name,
            "proactive_review.yaml",
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
    finally:
        try:
            os.unlink(temporary_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass


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
    normalized_issue_name = _normalized_issue_name(issue_name)
    candidate = _candidate_document(
        project_root=project_root,
        playbook_id=playbook_id,
        decision_values=decision_values,
        confirmed_by=confirmed_by,
        confirmed_at=confirmed_at,
    )
    with _open_contract_directory(
        project_root=project_root,
        issue_name=normalized_issue_name,
    ) as directory_fd:
        if not _contract_exists(directory_fd=directory_fd):
            _replace_document(
                directory_fd=directory_fd,
                document=candidate,
                project_root=project_root,
            )
            return "created"
        try:
            existing = _read_existing_contract(
                directory_fd=directory_fd,
                project_root=project_root,
            )
        except FileNotFoundError:
            if not replacement_confirmed:
                raise
            _replace_document(
                directory_fd=directory_fd,
                document=candidate,
                project_root=project_root,
            )
            return "replaced"
        except (ValueError, yaml.YAMLError):
            if not replacement_confirmed:
                raise
            _replace_document(
                directory_fd=directory_fd,
                document=candidate,
                project_root=project_root,
            )
            return "replaced"
        if _semantic_contract(existing) == _semantic_contract(candidate):
            return "reused"
        if not replacement_confirmed:
            raise ValueError(
                "changed proactive review contract requires complete replacement confirmation"
            )
        _replace_document(
            directory_fd=directory_fd,
            document=candidate,
            project_root=project_root,
        )
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
