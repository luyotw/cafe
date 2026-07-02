"""Builtin hook scaffolding for GenericPhase."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

from cafe.core.status_codes import PhaseStatusCode


@dataclass
class HookResult:
    """Standard hook return contract."""

    continue_pipeline: bool = True
    retry_requested: bool = False
    artifact_ready: bool = True
    override_status_code: Optional[PhaseStatusCode] = None
    context_updates: Dict[str, str] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)


class Hook(Protocol):
    """Protocol for one lifecycle hook."""

    name: str

    def run(self, **kwargs: Any) -> HookResult:
        """Execute hook logic."""


class NoOpHook:
    """Default scaffold hook that does nothing."""

    name = "NoOpHook"

    def run(self, **kwargs: Any) -> HookResult:
        return HookResult()


class InteractiveQAHandler(NoOpHook):
    name = "InteractiveQAHandler"


class PermissionRetryHandler(NoOpHook):
    name = "PermissionRetryHandler"


class NewChangesGate(NoOpHook):
    name = "NewChangesGate"


class GitHubPRCreator(NoOpHook):
    name = "GitHubPRCreator"


class PRCommentPoster(NoOpHook):
    name = "PRCommentPoster"


class LocalPRReviewer(NoOpHook):
    name = "LocalPRReviewer"


class PRLinkOpener(NoOpHook):
    name = "PRLinkOpener"


from cafe.core.hooks.native import (
    GitHubIssueFetcher,
    GitHubPRCreator,
    LocalPRReviewer,
    NoChangesNeededHandler,
    PRCommentPoster,
    PRLinkOpener,
    UserInputCollector,
)
from cafe.core.hooks.alignment import AlignmentCheckpointGate


BUILTIN_HOOKS = {
    hook.name: hook
    for hook in [
        GitHubIssueFetcher,
        UserInputCollector,
        NoChangesNeededHandler,
        InteractiveQAHandler,
        PermissionRetryHandler,
        NewChangesGate,
        GitHubPRCreator,
        LocalPRReviewer,
        PRCommentPoster,
        PRLinkOpener,
        AlignmentCheckpointGate,
    ]
}
