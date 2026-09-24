"""Pure topology projection and route authorization primitives.

The playbook remains the source of truth.  This module only derives an
ephemeral, prompt-safe view of its graph and the routes originating at one
step; it does not persist routing state or know any built-in step names.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import Any


def normalize_route_target(target: object) -> str:
    """Return the public target spelling shared by prompts and batons."""
    value = str(target)
    return "done" if value in {"done", "_done"} else value


@dataclass(frozen=True)
class RouteInputRequirement:
    """One actionable requirement satisfied by any declared candidate."""

    name: str
    candidates: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.candidates:
            raise ValueError("route input requirements need a name and candidates")


@dataclass(frozen=True)
class RouteReadiness:
    """Advisory availability snapshot for a target route."""

    ready: bool
    missing: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.ready and self.missing:
            raise ValueError("a ready route cannot declare missing requirements")
        if not self.ready and not self.missing:
            raise ValueError("an unready route must identify missing requirements")


@dataclass(frozen=True)
class RouteChoices:
    """Applicable default plus playbook-declared discretionary targets."""

    default: str | None
    goto: tuple[str, ...]

    @property
    def authorized(self) -> tuple[str, ...]:
        values = (() if self.default is None else (self.default,)) + self.goto
        return tuple(dict.fromkeys(values))

    def diagnostic(self) -> str:
        default = self.default if self.default is not None else "(none)"
        discretionary = ", ".join(self.goto) if self.goto else "(none)"
        return f"default route: {default}; discretionary routes: {discretionary}"


@dataclass(frozen=True)
class RouteProjection:
    """One bounded graph and the current step's compact route catalog."""

    graph: dict[str, Any]
    routes: dict[str, Any]


def _steps(playbook: Mapping[str, Any]) -> Mapping[str, Any]:
    value = playbook.get("steps", {})
    return value if isinstance(value, Mapping) else {}


def _step(playbook: Mapping[str, Any], step_name: str) -> Mapping[str, Any]:
    value = _steps(playbook).get(step_name, {})
    return value if isinstance(value, Mapping) else {}


def route_choices(
    playbook: Mapping[str, Any],
    *,
    current_step: str,
    intent: str,
) -> RouteChoices:
    """Resolve the route choices authorized for one semantic intent."""
    step = _step(playbook, current_step)
    transitions = step.get("on", {})
    mapped: object | None = None
    if isinstance(transitions, Mapping):
        mapped = transitions.get(intent)
        if mapped is None:
            mapped = transitions.get("default")
    default = normalize_route_target(mapped) if mapped is not None else None
    raw_goto = step.get("allowed_goto", ())
    goto = (
        tuple(
            dict.fromkeys(
                normalize_route_target(target) for target in raw_goto if str(target).strip()
            )
        )
        if isinstance(raw_goto, (list, tuple))
        else ()
    )
    return RouteChoices(default=default, goto=goto)


def authorize_route_target(
    playbook: Mapping[str, Any],
    *,
    current_step: str,
    intent: str,
    target: str,
) -> bool:
    """Whether the selected target is default-mapped or discretionary."""
    return (
        normalize_route_target(target)
        in route_choices(
            playbook,
            current_step=current_step,
            intent=intent,
        ).authorized
    )


def select_route_label(
    *,
    target: str,
    handoff_label: str | None,
    skill_descriptions: Iterable[str | None],
    role_description: str | None,
) -> str:
    """Apply the declared deterministic route-label fallback tiers."""
    candidates = (handoff_label, *skill_descriptions, role_description, target)
    for candidate in candidates:
        value = str(candidate or "").strip()
        if value:
            return value
    return target


def evaluate_route_readiness(
    requirements: Iterable[RouteInputRequirement],
    *,
    available_artifacts: Collection[str],
) -> RouteReadiness:
    """Evaluate required alternative groups; optional inputs are not supplied."""
    available = set(available_artifacts)
    missing = tuple(
        requirement.name
        for requirement in requirements
        if not available.intersection(requirement.candidates)
    )
    return RouteReadiness(ready=not missing, missing=missing)


def _graph_projection(playbook: Mapping[str, Any]) -> dict[str, Any]:
    steps = _steps(playbook)
    projected_steps: list[dict[str, Any]] = []
    for step_name, raw_step in steps.items():
        step = raw_step if isinstance(raw_step, Mapping) else {}
        transitions = step.get("on", {})
        defaults = (
            [
                {
                    "intent": str(intent),
                    "to": normalize_route_target(target),
                }
                for intent, target in transitions.items()
            ]
            if isinstance(transitions, Mapping)
            else []
        )
        raw_goto = step.get("allowed_goto", ())
        goto = (
            [normalize_route_target(target) for target in raw_goto]
            if isinstance(raw_goto, (list, tuple))
            else []
        )
        projected_steps.append(
            {
                "from": str(step_name),
                "defaults": defaults,
                "goto": goto,
            }
        )
    entry = playbook.get("entry_point") or next(iter(steps), "")
    return {"entry": str(entry), "steps": projected_steps}


def _route_entry(
    target: str,
    *,
    labels: Mapping[str, str],
    readiness: Mapping[str, RouteReadiness],
    feedback_targets: Collection[str],
) -> dict[str, Any]:
    state = readiness.get(target, RouteReadiness(ready=True))
    entry: dict[str, Any] = {
        "to": target,
        "label": str(labels.get(target) or target),
        "ready": state.ready,
    }
    if state.missing:
        entry["missing"] = list(state.missing)
    if target in feedback_targets:
        entry["carries_feedback"] = True
    return entry


def build_route_projection(
    playbook: Mapping[str, Any],
    *,
    current_step: str,
    labels: Mapping[str, str],
    readiness: Mapping[str, RouteReadiness],
    feedback_targets: Collection[str] = (),
) -> RouteProjection:
    """Build the graph once and routes only for the current source step."""
    step = _step(playbook, current_step)
    transitions = step.get("on", {})
    normalized_feedback = {normalize_route_target(target) for target in feedback_targets}
    defaults: dict[str, dict[str, Any]] = {}
    if isinstance(transitions, Mapping):
        for intent, raw_target in transitions.items():
            target = normalize_route_target(raw_target)
            defaults[str(intent)] = _route_entry(
                target,
                labels=labels,
                readiness=readiness,
                feedback_targets=normalized_feedback,
            )
    raw_goto = step.get("allowed_goto", ())
    goto = (
        [
            _route_entry(
                normalize_route_target(raw_target),
                labels=labels,
                readiness=readiness,
                feedback_targets=normalized_feedback,
            )
            for raw_target in raw_goto
        ]
        if isinstance(raw_goto, (list, tuple))
        else []
    )
    return RouteProjection(
        graph=_graph_projection(playbook),
        routes={"defaults": defaults, "goto": goto},
    )
