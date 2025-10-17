"""Base class for workflow phases."""

from abc import ABC, abstractmethod

from aaf.core.types import PhaseResult


class Phase(ABC):
    """Abstract base class for all workflow phases.

    Each phase represents a step in the AAF workflow (e.g., requirements clarification,
    implementation analysis, development, code review, etc.).

    Subclasses must implement the execute() method to define the phase's behavior.
    """

    @abstractmethod
    def execute(self) -> PhaseResult:
        """Execute the phase and return the result.

        Returns:
            PhaseResult containing the status and any relevant data

        Raises:
            Any exceptions from phase execution will propagate to the caller
        """
        pass
