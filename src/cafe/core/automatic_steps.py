"""Closed, runtime-owned dispatch for deterministic automatic workflow steps."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class AutomaticExecutionResult:
    """The only automatic-executor result accepted by the workflow runtime."""

    intent: str
    artifacts: dict[str, str] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)


class AutomaticExecutorRegistry:
    """A closed map of host-supplied automatic executors.

    Playbook data selects an ID from this registry but cannot register a
    callable, executable path, module, or capability.  Keeping registration
    at runtime construction preserves the trusted-host boundary.
    """

    def __init__(
        self,
        executors: (
            Mapping[str, Callable[[Mapping[str, Any]], AutomaticExecutionResult]] | None
        ) = None,
        *,
        input_validators: Mapping[str, Callable[[Mapping[str, Any]], None]] | None = None,
    ) -> None:
        self._executors = dict(executors or {})
        self._input_validators = dict(input_validators or {})

    def is_registered(self, executor_id: str) -> bool:
        """Return whether a runtime-owned executor is available to this run."""
        return executor_id in self._executors

    def validate_inputs(self, executor_id: str, inputs: Mapping[str, Any]) -> None:
        """Reject invalid declared input before an automatic step records progress."""
        if executor_id not in self._executors:
            raise ValueError(f"automatic executor {executor_id!r} is not registered")
        validator = self._input_validators.get(executor_id)
        if validator is not None:
            validator(dict(inputs))

    def execute(self, executor_id: str, inputs: Mapping[str, Any]) -> AutomaticExecutionResult:
        executor = self._executors.get(executor_id)
        if executor is None:
            raise ValueError(f"automatic executor {executor_id!r} is not registered")
        result = executor(dict(inputs))
        if not isinstance(result, AutomaticExecutionResult):
            raise ValueError(f"automatic executor {executor_id!r} returned an invalid result")
        if not result.intent.strip():
            raise ValueError(f"automatic executor {executor_id!r} returned an empty intent")
        return result


def _declared_transition(inputs: Mapping[str, Any]) -> AutomaticExecutionResult:
    """A safe built-in executor for a declared, data-only transition."""
    _validate_declared_transition_inputs(inputs)
    return AutomaticExecutionResult(intent=str(inputs["intent"]).strip())


def _validate_declared_transition_inputs(inputs: Mapping[str, Any]) -> None:
    """Validate the built-in transition without running an automatic step."""
    intent = inputs.get("intent")
    if not isinstance(intent, str) or not intent.strip():
        raise ValueError("automatic transition executor requires a non-empty inputs.intent")


def default_automatic_executor_registry() -> AutomaticExecutorRegistry:
    """Return the closed set of native executors shipped by this runtime."""
    return AutomaticExecutorRegistry(
        {"declared_transition": _declared_transition},
        input_validators={"declared_transition": _validate_declared_transition_inputs},
    )
