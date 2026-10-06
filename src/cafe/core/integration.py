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
