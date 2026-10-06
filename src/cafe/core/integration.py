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


class IntegrationReviewRequired(IntegrationError):
    """The current source identity must return through the declared review route."""


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

    def __init__(
        self, issue_dir: Path, playbook: Mapping[str, Any], blackboard: Any, task_store: Any = None
    ):
        from cafe.core.integration_records import IntegrationRecordStore
        from cafe.core.human_task_records import HumanTaskRecordStore

        self.issue_dir = Path(issue_dir)
        self.blackboard = blackboard
        self.declaration = IntegrationDeclaration.model_validate(playbook["integration"])
        self.tasks = task_store or HumanTaskRecordStore(self.issue_dir)
        self.records = IntegrationRecordStore(self.issue_dir, blackboard.workflow_id, self.tasks)

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

        def receipt_matches(receipt: dict[str, Any]) -> bool:
            if receipt.get("capability") != "cafe.pr.publish" or receipt.get("success") is not True:
                return False
            output = (receipt.get("inputs") or {}).get("output")
            if not isinstance(output, str):
                return False
            published_path = Path(output)
            if not published_path.is_absolute():
                published_path = self.issue_dir.parent.parent.parent / published_path
            delivery_path = Path(delivery["path"])
            if not delivery_path.is_absolute():
                delivery_path = self.issue_dir.parent.parent.parent / delivery_path
            return published_path.resolve() == delivery_path.resolve()

        receipts = [r for r in self.blackboard.capability_receipts if receipt_matches(r)]
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
            raise IntegrationReviewRequired("Reviewed source changed; renewed review is required")
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
                raise IntegrationReviewRequired(
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
        revision = self.records.propose(
            selection.model_dump(mode="json"), review.model_dump(mode="json")
        )
        prefix = f"integration:{self.blackboard.workflow_id}:"
        for task in self.tasks.tasks():
            if (
                task.handoff_key.startswith(prefix)
                and not task.handoff_key.startswith(f"{prefix}{revision}:")
                and task.status.value == "pending"
            ):
                self.tasks.cancel(
                    workflow_id=self.blackboard.workflow_id,
                    task_id=task.id,
                    reason="Superseded by a newly proposed integration destination",
                )
        return revision

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
        context = json.dumps(
            {
                "selection_revision": selected["revision"],
                "destination": selected["selection"],
                "accepted_review": selected["review"],
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return (
            f"{prompt}\n\n{context}",
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

    def reconcile(self) -> None:
        """Repair exact task/result associations after cross-file interruptions."""
        selected = self.records.current(self.records.read())
        if selected is None:
            return
        prefix = f"integration:{self.blackboard.workflow_id}:{selected['revision']}:"
        results = {r.task_id: r for r in self.tasks.results()}
        for task in self.tasks.tasks():
            if task.handoff_key.startswith(prefix):
                self.associate(task)
                if task.id in results:
                    self.apply_result(task, results[task.id])

    def selected(self) -> tuple[dict[str, Any], IntegrationSelection]:
        review = self.validate_current_review()
        record = self.records.read()
        selected = self.records.current(record)
        if selected is None or selected["review"] != review.model_dump(mode="json"):
            raise IntegrationError("Current selection needs the accepted source review")
        confirmation = selected.get("confirmation")
        if not isinstance(confirmation, dict):
            raise IntegrationError("Confirm the exact integration destination first")
        task = self.tasks.get_task(confirmation["task_id"])
        result = self.tasks.get_result(task.id)
        if (
            result is None
            or result.id != confirmation["result_id"]
            or result.payload.get("decision") != "confirm"
            or task.workflow_id != self.blackboard.workflow_id
            or task.handoff_key
            != f"integration:{self.blackboard.workflow_id}:{selected['revision']}:confirmation"
        ):
            raise IntegrationError("Current destination lacks its durable human confirmation")
        return selected, IntegrationSelection.model_validate(selected["selection"])

    def verify(self) -> dict[str, Any]:
        from cafe.utils.github import GitHubOps, GitHubError

        self.reconcile()
        selected, selection = self.selected()
        from cafe.core.git import GitOperations

        review, snapshot = self.review()
        feature = snapshot["feature_branch"]
        from cafe.core.git import GitError

        try:
            current_source = GitOperations(review.repository).run_git(
                "show-ref", "--verify", "--hash", f"refs/heads/{feature}"
            )
        except GitError:
            current_source = None  # A deleted historical feature ref is permitted.
        if current_source is not None and current_source != review.source_commit:
            raise IntegrationReviewRequired("Feature source changed; renewed review is required")
        try:
            if selection.target == "github_pr":
                observed = GitHubOps().observe_integration(selection.repository, selection.pr)
                success, reason = evaluate_github(selection, observed)
            else:
                from cafe.core.git import GitOperations

                observed = GitOperations(selection.repository).observe_integration(
                    selection.target_branch, selection.source_commit
                )
                success, reason = evaluate_local(selection, observed)
        except (GitHubError, OSError, ValueError) as exc:
            observed = {"unavailable": True}
            success, reason = False, str(exc)[:1000]
        # The caller's snapshot is fenced again after all process/network I/O.
        review = self.validate_current_review()
        if review.model_dump(mode="json") != selected["review"]:
            raise IntegrationError("Review changed during inspection; discard stale observation")
        return self.records.record_attempt(
            selected["revision"],
            {
                "success": success,
                "reason": reason,
                "observed": observed,
            },
        )

    def status(self) -> dict[str, Any]:
        """Pure projection: read durable facts without Git, network or reconciliation."""
        record = self.records.read()
        selected = self.records.current(record)
        review = None
        try:
            accepted, _ = self.review()
            review = accepted.model_dump(mode="json")
        except ValueError:
            pass
        reports = [
            r for r in record["reports"] if selected and r["revision"] == selected["revision"]
        ]
        attempts = [
            a for a in record["attempts"] if selected and a["revision"] == selected["revision"]
        ]
        report = reports[-1] if reports else None
        proof = attempts[-1] if attempts else None
        state, reason, action = (
            "awaiting_review",
            "Accepted source review is required",
            "Complete the declared review task",
        )
        if review is not None:
            state, reason, action = (
                "review_accepted",
                "Choose an explicit destination",
                "cafe integration select",
            )
        if selected:
            state, reason, action = (
                "pending_confirmation",
                "Destination proposal awaits human confirmation",
                "cafe task inspect",
            )
            if selected["confirmation"]:
                state, reason, action = (
                    "pending_action",
                    "Human integration is required",
                    "cafe task inspect",
                )
            if report:
                state = (
                    "blocked" if report["outcome"] == "blocked" else "reported_pending_verification"
                )
                reason = (
                    "Human reported a conflict or inability to integrate"
                    if state == "blocked"
                    else "Human report awaits read-only proof"
                )
                action = (
                    "Resolve human work, then cafe integration verify"
                    if state == "blocked"
                    else "cafe integration verify"
                )
            if proof and (report is None or proof.get("report_result_id") == report["result_id"]):
                state, reason, action = (
                    "verification_failed",
                    proof["reason"],
                    "cafe integration verify",
                )
                if self.completion_allowed():
                    state, action = "verified", "cafe workflow --execute"
            if review != selected["review"]:
                state, reason, action = (
                    "review_required",
                    "Current source review no longer matches selection",
                    "Resume the declared correction/review journey",
                )
        completed = self.blackboard.current_step == "done" and self.completion_allowed(
            completed=True
        )
        if completed:
            action = "No remaining integration work"
        return {
            "state": state,
            "workflow_id": self.blackboard.workflow_id,
            "review": review,
            "selection": selected["selection"] if selected else None,
            "selection_revision": selected["revision"] if selected else None,
            "confirmed": bool(selected and selected["confirmation"]),
            "task_ids": selected["tasks"] if selected else {},
            "report": report,
            "proof": proof,
            "reason": reason,
            "next_action": action,
            "completed": completed,
        }

    def completion_allowed(self, *, completed: bool = False) -> bool:
        try:
            selected, selection = self.selected()
            record = self.records.read()
            if not self.records.qualifies(record):
                return False
            last = [a for a in record["attempts"] if a["revision"] == selected["revision"]][-1]
            evaluator = evaluate_github if selection.target == "github_pr" else evaluate_local
            if not evaluator(selection, last["observed"])[0]:
                return False
            if completed:
                association = record.get("completion")
                return (
                    isinstance(association, dict)
                    and association.get("revision") == selected["revision"]
                    and association.get("attempt_id") == last["id"]
                )
            return True
        except (ValueError, KeyError, TypeError, OSError):
            return False


def evaluate_github(
    selection: IntegrationSelection, observed: Mapping[str, Any]
) -> tuple[bool, str]:
    if observed.get("unavailable"):
        return (
            False,
            "GitHub inspection unavailable; restore access and retry cafe integration verify",
        )
    for field, expected in (
        ("repository", selection.repository),
        ("pr", selection.pr),
        ("source_commit", selection.source_commit),
        ("target_branch", selection.target_branch),
    ):
        if observed.get(field) != expected:
            return False, f"Observed PR {field} differs from the confirmed reviewed destination"
    if observed.get("merged") is not True or observed.get("state") != "closed":
        return False, "The confirmed PR is not merged; human integration is still required"
    commit = observed.get("merge_commit")
    if not isinstance(commit, str) or not _SHA.fullmatch(commit):
        return False, "The merged PR has no valid merge commit identity"
    return True, "Exact reviewed PR is merged into the confirmed destination"


def evaluate_local(
    selection: IntegrationSelection, observed: Mapping[str, Any]
) -> tuple[bool, str]:
    if observed.get("unavailable"):
        return (
            False,
            "Local inspection unavailable; restore local branch/source objects and retry verification",
        )
    for field, expected in (
        ("repository", str(Path(selection.repository).resolve())),
        ("target_branch", selection.target_branch),
        ("source_commit", selection.source_commit),
    ):
        if observed.get(field) != expected:
            return False, f"Observed local {field} differs from the confirmed reviewed destination"
    head = observed.get("target_head")
    if not isinstance(head, str) or not _SHA.fullmatch(head):
        return False, "Named local target branch has no valid commit"
    if observed.get("ancestor_exit_code") != 0 or observed.get("stable") is not True:
        return False, "Approved source is not an ancestor of the stable named local target"
    return True, "Approved source is the named local target HEAD or its ancestor"


def integration_service(
    issue_dir: Path, playbook: Mapping[str, Any], blackboard: Any, task_store: Any = None
) -> IntegrationService | None:
    if not playbook.get("integration"):
        return None
    return IntegrationService(issue_dir, playbook, blackboard, task_store)
