"""Provider-neutral, single-attempt conversation operations."""

import subprocess
from dataclasses import replace
from typing import Callable

from cafe.agents.diagnostics import sanitize_error_excerpt
from cafe.agents.executor import AgentExecutionError, AgentExecutor
from cafe.agents.transport_types import Evidence, Operation, TransportCapabilities, TransportResult
from cafe.core.types import TokenUsage


class ConversationTransport:
    """Use an existing caller-selected executor without choosing recovery."""

    def __init__(self, executor: AgentExecutor):
        self.executor = executor

    def capabilities(self, operation: Operation) -> TransportCapabilities:
        return self.executor._get_cli_strategy().conversation_capabilities(operation)

    def _admit(self, operation, required_evidence):
        capabilities = self.capabilities(operation)
        if not capabilities.supported or any(
            name not in {"session", "model", "usage", "acceptance"}
            or not getattr(capabilities, name) for name in required_evidence
        ):
            self._fail(TransportResult(failure_code="unsupported", accepted=False))

    @staticmethod
    def _fail(result):
        error = AgentExecutionError(result.error_excerpt or result.failure_code,
                                    error_type=result.failure_code)
        error.transport_result = result
        raise error

    def _callback(self, prompt, *, session_id=None, delivery_id=None,
                  required_evidence=frozenset(), on_usage=None, **kwargs):
        operation = "deliver_to_exact_session" if session_id is not None else "acquire_session"
        inherent = {"session", "acceptance"} if session_id is not None else {"session"}
        self._admit(operation, required_evidence | inherent)
        previous = self.executor.config.session_id
        self.executor.config.session_id = session_id
        try:
            executed = self.executor.execute_event_driver(
                prompt, expected_session_id=session_id, event_id=delivery_id, **kwargs)
            result = executed.transport_result
        except AgentExecutionError as error:
            result = getattr(error, "transport_result", TransportResult(
                failure_code=error.error_type or "execution_failed",
                error_excerpt=sanitize_error_excerpt(error),
                accepted=False if session_id is None else None,
            ))
            if on_usage is not None and result.usage is not None:
                on_usage(result.usage)
            error.transport_result = result
            raise
        finally:
            self.executor.config.session_id = previous
        if on_usage is not None and result.usage is not None:
            on_usage(result.usage)
        if session_id is None:
            result = replace(result, accepted=False)
        missing = any(getattr(result, {
            "session": "observed_session_id", "model": "reported_model",
            "usage": "usage", "acceptance": "accepted",
        }[name]) is None for name in required_evidence | inherent)
        if result.failure_code or missing:
            self._fail(replace(result, failure_code=result.failure_code or "missing_evidence"))
        if session_id is not None and result.observed_session_id != session_id:
            self._fail(replace(result, failure_code="session_mismatch"))
        return result

    def acquire_session(self, prompt: str, **kwargs) -> TransportResult:
        return self._callback(prompt, **kwargs)

    def deliver_to_exact_session(self, prompt: str, session_id: str, delivery_id: str,
                                **kwargs) -> TransportResult:
        if not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 512:
            raise ValueError("exact destination must be a bounded nonempty identity")
        if not isinstance(delivery_id, str) or not delivery_id.strip() or delivery_id not in prompt:
            raise ValueError("delivery requires a correlation identity in its prompt")
        return self._callback(prompt, session_id=session_id, delivery_id=delivery_id, **kwargs)

    def open_interactive_session(self, initial_prompt=None, *, required_evidence=frozenset(),
                                 environment_overrides=None) -> TransportResult:
        self._admit("open_interactive_session", required_evidence)
        strategy = self.executor._get_cli_strategy()
        environment = strategy.build_environment()
        if environment_overrides:
            environment.update({str(key): str(value) for key, value in environment_overrides.items()})
        try:
            process = subprocess.run(strategy.build_interactive_command(initial_prompt), env=environment)
        except OSError as cause:
            result = TransportResult(failure_code="cli_not_found" if isinstance(cause, FileNotFoundError)
                                     else "launch_failed", accepted=False,
                                     error_excerpt=sanitize_error_excerpt(cause))
            self._fail(result)
        failure = None if process.returncode == 0 else "execution_failed"
        diagnostic = getattr(process, "stderr", None) or getattr(process, "stdout", None)
        return TransportResult(returncode=process.returncode, failure_code=failure,
                               error_excerpt=sanitize_error_excerpt(Exception(diagnostic))
                               if isinstance(diagnostic, str) and diagnostic else None)

    def run_one_shot(self, prompt: str, *, required_evidence=frozenset(), on_response=None,
                     on_usage=None, **kwargs) -> TransportResult:
        self._admit("run_one_shot", required_evidence)
        selected_session = self.executor.config.session_id
        try:
            response = self.executor.execute(prompt, exact_session=True, **kwargs)
        except AgentExecutionError as error:
            self.executor.config.session_id = selected_session
            result = getattr(error, "transport_result", None) or TransportResult(
                failure_code=error.error_type or "execution_failed",
                error_excerpt=sanitize_error_excerpt(error))
            if on_usage is not None and result.usage is not None:
                on_usage(result.usage)
            error.transport_result = result
            raise
        result = response.transport_result
        if not isinstance(result, TransportResult):
            result = TransportResult()
        if selected_session and result.observed_session_id and result.observed_session_id != selected_session:
            result = replace(result, failure_code="session_mismatch")
        if on_usage is not None and result.usage is not None:
            on_usage(result.usage)
        missing = any(getattr(result, {
            "session": "observed_session_id", "model": "reported_model",
            "usage": "usage", "acceptance": "accepted",
        }[name]) is None for name in required_evidence)
        if result.failure_code or missing:
            self.executor.config.session_id = selected_session
            self._fail(replace(result, failure_code=result.failure_code or "missing_evidence"))
        if on_response is not None:
            on_response(response)
        return result
