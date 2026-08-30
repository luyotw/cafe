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
    prompt_instructions: List[str] = field(default_factory=list)
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


class GitHubPRFeedbackSource(NoOpHook):
    name = "GitHubPRFeedbackSource"


class PRCommentPoster(NoOpHook):
    name = "PRCommentPoster"


class LocalReviewContextProvider(NoOpHook):
    name = "LocalReviewContextProvider"


class PRLinkOpener(NoOpHook):
    name = "PRLinkOpener"


from cafe.core.hooks.native import (
    GitHubIssueFetcher,
    GitHubPRCreator,
    InitialInputProviderResolver,
    NoChangesNeededHandler,
    PRCommentPoster,
    PRLinkOpener,
    UserInputCollector,
)
from cafe.core.hooks.alignment import AlignmentCheckpointGate
from cafe.core.hooks.feedback import GitHubPRFeedbackSource, LocalReviewContextProvider
from cafe.core.hooks.review import ReviewDiscoveryHook


BUILTIN_HOOKS = {
    hook.name: hook
    for hook in [
        GitHubIssueFetcher,
        InitialInputProviderResolver,
        UserInputCollector,
        NoChangesNeededHandler,
        InteractiveQAHandler,
        PermissionRetryHandler,
        NewChangesGate,
        GitHubPRCreator,
        GitHubPRFeedbackSource,
        LocalReviewContextProvider,
        PRCommentPoster,
        PRLinkOpener,
        AlignmentCheckpointGate,
        ReviewDiscoveryHook,
    ]
}


def resolve_skill_hook_class(
    hook_registry: Dict[str, type],
    *,
    name: str,
    stage: str,
) -> type:
    """Resolve one host hook only when it explicitly permits skill selection."""
    hook_cls = hook_registry.get(name)
    if hook_cls is None:
        raise ValueError(f"Unknown skill runtime hook '{name}' in stage '{stage}'")
    if stage not in getattr(hook_cls, "skill_stages", ()):
        raise ValueError(
            f"Hook '{name}' cannot be declared by a skill in stage '{stage}'"
        )
    return hook_cls
