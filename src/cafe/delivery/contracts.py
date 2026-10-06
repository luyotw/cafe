"""Immutable, exact action proposals and correlated selection authority."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cafe.core.packet_io import canonical_json


def digest(value: dict) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DeliveryBinding(FrozenModel):
    """Names come from the adopting graph, never from a phase-name registry."""

    actions_artifact: str
    result_artifact: str
    approval_step: str
    approval_task: str = "delivery-review"
    result_task: str = "delivery-outcome"
    correction_step: str
    proposals_artifact: str | None = None


class FollowUp(FrozenModel):
    id: str = Field(pattern=r"^FUP-[0-9]{3}$")
    title: str = Field(min_length=1, max_length=256)
    body: str = Field(min_length=1, max_length=32768)
    evidence: str = Field(min_length=1, max_length=4096)


class ActionProposal(FrozenModel):
    version: Literal[1] = 1
    workflow_id: str = Field(min_length=1)
    approval_step: str = Field(min_length=1)
    approval_iteration: int = Field(ge=1)
    repository: str = Field(min_length=1)
    source_oid: str = Field(pattern=r"^[0-9a-f]{40}$")
    source_branch: str = Field(min_length=1)
    target_branch: str = Field(min_length=1)
    target_oid: str = Field(pattern=r"^[0-9a-f]{40}$")
    mode: Literal["github", "local"]
    strategy: Literal["merge", "squash", "rebase", "ff-only", "merge-commit"]
    pr_number: int | None = Field(default=None, ge=1)
    destination: str = ""
    issue_repository: str = ""
    proposals: tuple[FollowUp, ...] = Field(default=(), max_length=100)
    reviewed_artifact: str = ""
    reviewed_artifact_sha256: str = Field(default="", pattern=r"^(?:[0-9a-f]{64})?$")

    @model_validator(mode="after")
    def exact_binding(self):
        if self.mode == "github" and not re.fullmatch(
            r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.repository
        ):
            raise ValueError("invalid GitHub repository")
        if self.mode == "local" and not Path(self.repository).is_absolute():
            raise ValueError("local repository must bind its absolute Git common directory")
        for branch in (self.source_branch, self.target_branch):
            if (
                branch.startswith(("-", "/"))
                or branch.endswith(("/", ".", ".lock"))
                or any(token in branch for token in ("..", "@{", "//"))
                or re.search(r"[\s~^:?*\[\\\x00-\x1f]", branch)
            ):
                raise ValueError("unsafe branch identity")
        if self.mode == "github":
            if (
                self.pr_number is None
                or self.strategy not in {"merge", "squash", "rebase"}
                or self.destination
            ):
                raise ValueError("GitHub integration binding is incomplete")
        elif (
            not Path(self.destination).is_absolute()
            or self.strategy not in {"ff-only", "merge-commit"}
            or self.pr_number is not None
        ):
            raise ValueError("local integration binding is incomplete")
        if self.issue_repository and not re.fullmatch(
            r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.issue_repository
        ):
            raise ValueError("invalid issue destination")
        if len({p.id for p in self.proposals}) != len(self.proposals):
            raise ValueError("duplicate proposal identity")
        return self

    @property
    def digest(self) -> str:
        return digest(self.model_dump(mode="json"))


class ActionSnapshot(FrozenModel):
    proposal: ActionProposal
    proposal_digest: str
    task_id: str = Field(min_length=1)
    result_id: str = Field(min_length=1)
    selected: tuple[FollowUp, ...]

    @model_validator(mode="after")
    def frozen_selection(self):
        if self.proposal_digest != self.proposal.digest:
            raise ValueError("stale proposal bytes")
        available = {p.id: p for p in self.proposal.proposals}
        if len({p.id for p in self.selected}) != len(self.selected) or any(
            available.get(p.id) != p for p in self.selected
        ):
            raise ValueError("selection does not match shown drafts")
        if self.selected and not self.proposal.issue_repository:
            raise ValueError("selected issues need exact destination authority")
        return self

    @property
    def digest(self) -> str:
        return digest(self.model_dump(mode="json"))

    def marker(self, proposal_id: str) -> str:
        if proposal_id not in {p.id for p in self.selected}:
            raise ValueError("proposal was not selected")
        item = next(p for p in self.selected if p.id == proposal_id)
        identity = digest(
            {
                "workflow": self.proposal.workflow_id,
                "repository": self.proposal.issue_repository,
                "draft": item.model_dump(mode="json"),
            }
        )
        return f"<!-- cafe-follow-up:{self.proposal.workflow_id}:{identity}:{proposal_id} -->"


def approve_selection(proposal: ActionProposal, authority: dict) -> ActionSnapshot:
    expected = (
        proposal.workflow_id,
        proposal.approval_step,
        proposal.approval_iteration,
        proposal.digest,
    )
    actual = tuple(
        authority.get(k) for k in ("workflow_id", "step", "iteration", "proposal_digest")
    )
    if actual != expected or not authority.get("task_id") or not authority.get("result_id"):
        raise ValueError("uncorrelated action authority")
    decision = authority.get("decision")
    if decision == "integrate_only":
        ids = []
        if str(authority.get("feedback", "")).strip():
            raise ValueError("empty-selection approval cannot include other instructions")
    elif decision == "integrate_selected":
        ids = re.split(r"[\s,]+", str(authority.get("feedback", "")).strip())
        if not ids or not all(re.fullmatch(r"FUP-[0-9]{3}", item) for item in ids):
            raise ValueError("select explicit shown proposal IDs")
    else:
        raise ValueError("review acceptance is not action authority")
    available = {p.id: p for p in proposal.proposals}
    if len(set(ids)) != len(ids) or set(ids) - available.keys():
        raise ValueError("unknown or duplicate selected proposal")
    return ActionSnapshot(
        proposal=proposal,
        proposal_digest=proposal.digest,
        task_id=authority["task_id"],
        result_id=authority["result_id"],
        selected=tuple(available[item] for item in ids),
    )
