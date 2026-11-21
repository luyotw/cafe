"""Task-bound inputs shared by inbox inspection and HumanTask completion."""

from __future__ import annotations

import os
import stat
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from cafe.core.blackboard import ArtifactKind, BlackboardState, HandoffOwner
from cafe.core.human_task_records import HumanTask, HumanTaskStatus
from cafe.core.human_tasks import HumanTaskPolicy, HumanTaskQuestion
from cafe.core.packet_io import canonical_json, sha256_bytes
from cafe.core.questions_schema import Question, parse_validated_questions_xml

_MAX_INPUT_BYTES = 1024 * 1024


def durable_task_matches_current_handoff(
    task: HumanTask, blackboard: BlackboardState, *, require_exact_handoff: bool = False
) -> bool:
    contract = getattr(blackboard, "handoff_contract", None)
    if (
        getattr(blackboard, "current_step", None) != "user"
        or contract is None
        or contract.to_owner is not HandoffOwner.USER
        or contract.to_step != "user"
        or contract.from_step != task.step
    ):
        return False
    if task.handoff_key.startswith("user-handoff:"):
        # Structured keys also identify handoffs whose policy trigger differs from the intent.
        return task.handoff_key == ":".join(
            ("user-handoff", task.workflow_id, contract.from_step, contract.intent.value,
             contract.created_at)
        )
    return (
        not require_exact_handoff
        and task.status is HumanTaskStatus.PENDING
        and contract.intent.value == task.trigger
    )


def uses_special_approval(task: HumanTask, step_definition: Mapping[str, Any]) -> bool:
    if task.capability_approval is not None:
        return True
    delivery = step_definition.get("delivery")
    if not delivery:
        return False
    from cafe.delivery.contracts import DeliveryBinding

    binding = DeliveryBinding.model_validate(delivery)
    return task.policy_id in {binding.approval_task, binding.result_task}


@dataclass(frozen=True)
class ReviewArtifact:
    path: str
    sha256: str
    text: str
    registered_version: int | None


@dataclass(frozen=True)
class TaskInputs:
    answer_contract: tuple[HumanTaskQuestion, ...] | None
    questions_sha256: str | None
    reviews_output: bool
    review_artifact: ReviewArtifact | None

    def accepted_evidence(self, version: str, handoff_created_at: str) -> dict[str, Any]:
        """Keep the accepted input identity and reviewed bytes with the result."""
        return {
            "version": version,
            "handoff_created_at": handoff_created_at,
            "questions_sha256": self.questions_sha256,
            "review_artifact": asdict(self.review_artifact) if self.review_artifact else None,
        }

    @property
    def questions(self) -> tuple[Question, ...] | None:
        if self.answer_contract is None:
            return None
        return tuple(
            Question(q.id, q.prompt, list(q.options), q.multiple) for q in self.answer_contract
        )

    def snapshot_version(self, task: HumanTask, blackboard: BlackboardState) -> str | None:
        if self.answer_contract == () or (self.reviews_output and self.review_artifact is None):
            return None
        artifact = self.review_artifact
        contract = blackboard.handoff_contract
        return sha256_bytes(canonical_json({
            "schema_version": 1,
            "task_id": task.id,
            "workflow_id": task.workflow_id,
            "step": task.step,
            "iteration": task.iteration,
            "trigger": task.trigger,
            "handoff_key": task.handoff_key,
            "handoff_created_at": contract.created_at if contract else None,
            "prompt": task.prompt,
            "expected_result": task.expected_result,
            "continuations": task.continuations,
            "questions_sha256": self.questions_sha256,
            "review_artifact": {
                "path": artifact.path,
                "sha256": artifact.sha256,
                "registered_version": artifact.registered_version,
            } if artifact else None,
        }))

    def to_snapshot(self, task: HumanTask, blackboard: BlackboardState) -> dict[str, Any]:
        version = self.snapshot_version(task, blackboard)
        presented_questions = self.questions
        if presented_questions is None:
            questions = {"status": "not_applicable"}
        elif not presented_questions:
            questions = {"status": "unavailable"}
        else:
            questions = {"status": "available", "items": [asdict(q) for q in presented_questions]}
        if self.review_artifact:
            artifact = {"status": "available", **asdict(self.review_artifact)}
        else:
            artifact = {"status": "unavailable" if self.reviews_output else "not_applicable"}
        return {
            "schema_version": 1,
            "status": "available" if version else "unavailable",
            "version": version,
            "questions": questions,
            "review_artifact": artifact,
        }


def resolve_task_inputs(
    issue_dir: Path,
    *,
    step: str,
    iteration: int,
    policy: HumanTaskPolicy,
    blackboard: BlackboardState,
    step_definition: Mapping[str, Any],
) -> TaskInputs:
    """Read the declared inputs from the exact step and iteration."""
    directory = Path(step) / f"iteration_{iteration:03d}"
    questions_sha256 = None
    answer_contract = None
    if policy.questions_from_xml:
        answer_contract = policy.questions
        if not answer_contract:
            answer_contract, questions_sha256 = _read_questions(
                issue_dir, directory / "questions.xml"
            )
    reviews_output = policy.pattern in {"confirm_output", "no_changes_needed"}
    artifact = None
    if reviews_output:
        artifact = _read_review_artifact(
            issue_dir, directory / "output.md", step, blackboard, step_definition,
        )
    return TaskInputs(answer_contract, questions_sha256, reviews_output, artifact)


def _read_questions(
    issue_dir: Path, path: Path,
) -> tuple[tuple[HumanTaskQuestion, ...], str | None]:
    content = read_iteration_file(issue_dir, path)
    if content is not None:
        try:
            contract = tuple(
                HumanTaskQuestion(
                    id=q.id, prompt=q.title, options=q.options, multiple=q.multi_select,
                )
                for q in parse_validated_questions_xml(content)
            )
            if len({q.id for q in contract}) == len(contract):
                return contract, sha256_bytes(content)
        except ValueError:
            pass
    return (), None


def _read_review_artifact(
    issue_dir: Path, path: Path, step: str, blackboard: BlackboardState,
    step_definition: Mapping[str, Any],
) -> ReviewArtifact | None:
    name = step_definition.get("output_artifact")
    entry = blackboard.artifacts.get(name) if isinstance(name, str) else None
    if entry is not None:
        expected = (issue_dir / path).absolute()
        published = Path(entry.path)
        published_paths = {published.absolute(), (issue_dir / published).absolute()}
        if (entry.kind is not ArtifactKind.DOCUMENT or entry.updated_by != step
                or expected not in published_paths):
            return None

    content = read_iteration_file(issue_dir, path)
    if not content:
        return None
    digest = sha256_bytes(content)
    if entry is not None and entry.content_sha256 and entry.content_sha256 != digest:
        return None
    try:
        text = content.decode("utf-8")
    except UnicodeError:
        return None
    return ReviewArtifact(path.as_posix(), digest, text, entry.version if entry else None)


def read_iteration_file(issue_dir: Path, path: Path) -> bytes | None:
    """Read only a bounded, single-link regular file below the selected issue."""
    if path.is_absolute() or ".." in path.parts or len(path.parts) != 3:
        return None
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        directory = os.open(issue_dir, directory_flags)
        try:
            for part in path.parts[:-1]:
                child = os.open(part, directory_flags, dir_fd=directory)
                os.close(directory)
                directory = child
            fd = os.open(
                path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory,
            )
            with os.fdopen(fd, "rb") as source:
                before = os.fstat(source.fileno())
                if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                        or before.st_size > _MAX_INPUT_BYTES):
                    return None
                content = source.read(_MAX_INPUT_BYTES + 1)
                current = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
                if len(content) <= _MAX_INPUT_BYTES and (
                    before.st_dev, before.st_ino, before.st_ctime_ns
                ) == (current.st_dev, current.st_ino, current.st_ctime_ns):
                    return content
        finally:
            os.close(directory)
    except (OSError, ValueError):
        pass
    return None
