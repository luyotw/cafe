"""Mode-neutral selection, review and proof rules for post-review delivery.

This module never integrates changes. Human reports are distinct from observations
of the explicitly confirmed destination.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from cafe.core.playbook import IntegrationDeclaration

_SHA = re.compile(r"^[0-9a-f]{40}$")


class IntegrationError(ValueError):
    """Delivery remains incomplete until its declared prerequisite is restored."""


def valid_branch(value: str) -> str:
    if (
        not value
        or value.startswith(("-", "/", "refs/"))
        or value.endswith(("/", ".", ".lock"))
        or any(c in value for c in " ~^:?*[\\")
        or ".." in value
        or "@{" in value
        or "//" in value
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
        or any(part.startswith(".") or part.endswith(".lock") for part in value.split("/"))
    ):
        raise IntegrationError("Use an explicit short local branch name")
    return value


class IntegrationSelection(BaseModel):
    """Immutable destination proposal; confirmation belongs to a HumanTask."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    target: Literal["github_pr", "local_branch"]
    repository: str
    source_commit: str
    target_branch: str
    feature_branch: str | None = None
    pr: int | None = None

    @field_validator("source_commit")
    @classmethod
    def _source(cls, value: str) -> str:
        if not _SHA.fullmatch(value):
            raise IntegrationError("An approved full source commit is required")
        return value

    @field_validator("target_branch", "feature_branch")
    @classmethod
    def _branch(cls, value: str | None) -> str | None:
        return valid_branch(value) if value is not None else None

    @model_validator(mode="after")
    def _identity(self) -> IntegrationSelection:
        if self.target == "github_pr":
            if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.repository):
                raise IntegrationError("GitHub repository must be owner/name")
            if self.pr is None or self.pr < 1:
                raise IntegrationError("A published PR number is required")
        else:
            if not self.repository or not Path(self.repository).is_absolute():
                raise IntegrationError("An explicit absolute local repository is required")
            if (
                not self.feature_branch
                or self.feature_branch == self.target_branch
                or self.pr is not None
            ):
                raise IntegrationError("Local source and target branches must be distinct")
        return self


class AcceptedReview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    task_id: str
    result_id: str
    source_commit: str
    repository: str
    source_identity: str
    source_version: int


def accepted_review(
    declaration: IntegrationDeclaration,
    *,
    task_id: str,
    result_id: str,
    step: str,
    policy_id: str,
    decision: str,
    source: Mapping[str, Any],
    source_identity: str,
) -> AcceptedReview:
    if (
        step != declaration.review_step
        or policy_id != declaration.review_task
        or decision not in declaration.accepted_decisions
    ):
        raise IntegrationError("The declared review has not accepted this source")
    commit = source.get("head_sha")
    if (
        not isinstance(commit, str)
        or not _SHA.fullmatch(commit)
        or not task_id
        or not result_id
        or not source.get("repository")
        or not re.fullmatch(r"[0-9a-f]{64}", source_identity)
    ):
        raise IntegrationError("The accepted review source identity is incomplete")
    return AcceptedReview(
        task_id=task_id,
        result_id=result_id,
        source_commit=commit,
        repository=source["repository"],
        source_identity=source_identity,
        source_version=source["version"],
    )


class IntegrationService:
    """Neutral domain service; topology and task policies come from the catalog."""

    def __init__(self, issue_dir: Path, playbook: Mapping[str, Any], blackboard: Any):
        from cafe.core.integration_records import IntegrationRecordStore
        from cafe.core.human_task_records import HumanTaskRecordStore

        self.issue_dir = Path(issue_dir)
        self.blackboard = blackboard
        self.declaration = IntegrationDeclaration.model_validate(playbook["integration"])
        self.records = IntegrationRecordStore(self.issue_dir, blackboard.workflow_id)
        self.tasks = HumanTaskRecordStore(self.issue_dir)

    def _artifact(self, name: str, producer: str) -> tuple[dict[str, Any], bytes]:
        entry = self.blackboard.artifacts.get(name)
        if entry is None or entry.updated_by != producer:
            raise IntegrationError(
                f"Declared artifact {name!r} is missing or has the wrong producer"
            )
        path = Path(entry.path)
        if not path.is_absolute():
            # Artifact paths are repository-relative; the issue directory belongs
            # to that repository's .cafe/issues tree.
            path = self.issue_dir.parent.parent.parent / path
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise IntegrationError(f"Declared artifact {name!r} is unavailable") from exc
        return entry.to_dict(), content

    def capture_review(self, task_id: str) -> None:
        import json
        from cafe.core.packet_io import sha256_bytes
        from cafe.core.git import GitOperations
        from cafe.core.workspace_artifact import WorkspaceArtifact

        d = self.declaration
        entry, content = self._artifact(d.source_artifact, d.source_step)
        source = WorkspaceArtifact.from_dict(json.loads(content)).to_dict()
        if source["name"] != d.source_artifact or source["version"] != entry["version"]:
            raise IntegrationError("Reviewed source artifact identity is inconsistent")
        delivery, prepared = self._artifact(d.delivery_artifact, d.delivery_step)
        branch = GitOperations(source["repository"]).get_current_branch()
        valid_branch(branch)
        receipts = [
            r
            for r in self.blackboard.capability_receipts
            if r.get("capability") == "cafe.pr.publish"
            and r.get("success") is True
            and r.get("step") == d.delivery_step
        ]
        publication = receipts[-1] if receipts else None
        self.records.stage_review(
            task_id,
            {
                "source": source,
                "source_identity": sha256_bytes(content),
                "source_entry": entry,
                "feature_branch": branch,
                "delivery_entry": delivery,
                "delivery_identity": sha256_bytes(prepared),
                "publication": publication,
            },
        )

    def review(self) -> tuple[AcceptedReview, dict[str, Any]]:
        d = self.declaration
        record = self.records.read()
        candidates = [
            t
            for t in self.tasks.tasks()
            if t.workflow_id == self.blackboard.workflow_id
            and t.step == d.review_step
            and t.policy_id == d.review_task
        ]
        if not candidates:
            raise IntegrationError("Complete the declared review before selecting integration")
        task = candidates[-1]
        results = [r for r in self.tasks.results() if r.task_id == task.id]
        if not results:
            raise IntegrationError("The review has no durable accepted result")
        snapshot = record["reviews"].get(task.id)
        if snapshot is None:
            raise IntegrationError("Reviewed source snapshot is missing; return through review")
        result = results[-1]
        review = accepted_review(
            d,
            task_id=task.id,
            result_id=result.id,
            step=task.step,
            policy_id=task.policy_id,
            decision=result.payload.get("decision", ""),
            source=snapshot["source"],
            source_identity=snapshot["source_identity"],
        )
        return review, snapshot

    def validate_current_review(self) -> AcceptedReview:
        from cafe.core.packet_io import sha256_bytes

        review, snapshot = self.review()
        entry, content = self._artifact(
            self.declaration.source_artifact, self.declaration.source_step
        )
        if (
            entry["version"] != snapshot["source_entry"]["version"]
            or sha256_bytes(content) != review.source_identity
        ):
            raise IntegrationError("Reviewed source changed; renewed review is required")
        return review

    def propose(
        self,
        *,
        target: str,
        repository: str,
        target_branch: str,
        feature_branch: str | None = None,
        pr: int | None = None,
    ) -> str:
        review = self.validate_current_review()
        _, snapshot = self.review()
        if target == "local_branch":
            repository = str(Path(repository).resolve())
            if (
                repository != str(Path(review.repository).resolve())
                or feature_branch != snapshot["feature_branch"]
            ):
                raise IntegrationError(
                    "Repository or feature differs from the reviewed source; renew review"
                )
        selection = IntegrationSelection(
            target=target,
            repository=repository,
            source_commit=review.source_commit,
            target_branch=target_branch,
            feature_branch=feature_branch,
            pr=pr,
        )
        if target == "github_pr":
            publication = snapshot["publication"]
            if not isinstance(publication, dict):
                raise IntegrationError(
                    "GitHub integration requires the declared publication receipt"
                )
            outputs = publication.get("outputs") or {}
            expected_url = f"https://github.com/{repository}/pull/{pr}"
            if outputs.get("pr_url") != expected_url or str(outputs.get("pr_number")) != str(pr):
                raise IntegrationError("Selected PR does not match the reviewed publication")
        return self.records.propose(
            selection.model_dump(mode="json"), review.model_dump(mode="json")
        )

    def task_context(self, step: str, policy_id: str, prompt: str) -> tuple[str, str | None]:
        import json

        d = self.declaration
        if (step, policy_id) == (d.review_step, d.review_task):
            return prompt, None
        kind = (
            "confirmation"
            if (step, policy_id) == (d.selection_step, d.selection_task)
            else "action" if (step, policy_id) == (d.action_step, d.action_task) else None
        )
        if kind is None:
            return prompt, None
        review = self.validate_current_review()
        record = self.records.read()
        selected = self.records.current(record)
        if selected is None or selected["review"] != review.model_dump(mode="json"):
            raise IntegrationError("Stage an explicit destination with cafe integration select")
        if kind == "action" and selected["confirmation"] is None:
            raise IntegrationError("Confirm the destination task before human integration")
        context = json.dumps(selected["selection"], sort_keys=True, ensure_ascii=False)
        action = (
            "Confirm this exact destination."
            if kind == "confirmation"
            else "A human must integrate this approved source. Report performed, already_performed or blocked; CAFE only verifies."
        )
        return (
            f"{prompt}\n\n{action}\n{context}\nSelection revision: {selected['revision']}",
            f"integration:{self.blackboard.workflow_id}:{selected['revision']}:{kind}",
        )

    def associate(self, task: Any) -> None:
        d = self.declaration
        if (task.step, task.policy_id) == (d.review_step, d.review_task):
            if task.id not in self.records.read()["reviews"]:
                self.capture_review(task.id)
            return
        for kind, step, policy in (
            ("confirmation", d.selection_step, d.selection_task),
            ("action", d.action_step, d.action_task),
        ):
            if (task.step, task.policy_id) != (step, policy):
                continue
            selected = self.records.current(self.records.read())
            if (
                selected is None
                or task.handoff_key
                != f"integration:{self.blackboard.workflow_id}:{selected['revision']}:{kind}"
            ):
                raise IntegrationError("Task belongs to a superseded selection")
            self.records.associate_task(selected["revision"], task.id, kind)

    def apply_result(self, task: Any, result: Any) -> None:
        self.associate(task)
        d = self.declaration
        selected = self.records.current(self.records.read())
        if (task.step, task.policy_id) == (d.selection_step, d.selection_task):
            if result.payload.get("decision") == "confirm":
                self.validate_current_review()
                self.records.confirm(selected["revision"], task.id, result.id)
        elif (task.step, task.policy_id) == (d.action_step, d.action_task):
            self.records.report(
                selected["revision"], task.id, result.id, result.payload["decision"]
            )


def integration_service(
    issue_dir: Path, playbook: Mapping[str, Any], blackboard: Any
) -> IntegrationService | None:
    if not playbook.get("integration"):
        return None
    return IntegrationService(issue_dir, playbook, blackboard)
