"""Codex native fork review in one authenticated app-server process.

A parent turn requests review after its checkpoint. A separate native thread
follows the confirmed inspection-only policy and returns its independent result.
No standalone reviewer process or parent-authored completion is accepted.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty

from cafe.agents.cli.native_review import _PROMPT, _conclusion, validate_configuration
from cafe.agents.diagnostics import sanitize_error_excerpt
from cafe.agents.process_output import ProcessOutput
from cafe.agents.transport_types import TransportResult
from cafe.constraints import execution_context, numeric_limit
from cafe.core.cost import prepare_cost_accounting
from cafe.core.execution_checkpoints import review_read_only_enforcement
from cafe.core.types import AgentResponse, TokenUsage
from cafe.core.usage import merge_token_usage_stats

_COUNTERS = {
    "inputTokens": "input_tokens",
    "outputTokens": "output_tokens",
    "cachedInputTokens": "cache_read_input_tokens",
    "cacheWriteInputTokens": "cache_write_input_tokens",
    "reasoningOutputTokens": "reasoning_output_tokens",
}
_MAX_PROMPT = 128 * 1024
_DISABLED_FEATURES = (
    "apps",
    "plugins",
    "hooks",
    "multi_agent",
    "multi_agent_v2",
    "request_permissions_tool",
    "memories",
    "browser_use",
    "browser_use_external",
    "browser_use_full_cdp_access",
    "in_app_browser",
    "in_app_local_automation",
    "remote_plugin",
    "image_generation",
    "computer_use",
    "remote_control",
    "js_repl",
    "code_mode",
    "code_mode_host",
)


def _error(message, kind="native_review_unavailable"):
    from cafe.agents.executor import AgentExecutionError

    return AgentExecutionError(message, error_type=kind)


def _identity(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise _error("Codex returned an invalid native identity.")
    return value


def _counters(value):
    if not isinstance(value, dict):
        raise _error("Codex returned invalid token telemetry.", "invalid_evidence")
    result = {}
    for source, target in _COUNTERS.items():
        if source not in value:
            continue
        count = value[source]
        if type(count) is not int or count < 0:
            raise _error("Codex returned invalid token telemetry.", "invalid_evidence")
        result[target] = count
    if not {"input_tokens", "output_tokens"}.issubset(result):
        raise _error("Codex returned incomplete token telemetry.", "invalid_evidence")
    return result


def _review_request(text):
    """Only an exact structured parent request can launch the native fork."""
    try:
        value = json.loads(text)
    except (ValueError, RecursionError):
        return None
    if not isinstance(value, dict) or "cafe_native_review" not in value:
        return None
    request = value["cafe_native_review"]
    if (
        set(value) != {"cafe_native_review"}
        or not isinstance(request, dict)
        or set(request) != {"prompt"}
        or not isinstance(request["prompt"], str)
        or len(request["prompt"].encode()) > _MAX_PROMPT
    ):
        raise _error("Codex returned a malformed native review request.")
    markers = re.findall(r"CAFE_REVIEW_CHECKPOINT:([A-Za-z0-9-]+)", request["prompt"])
    if len(markers) != 1:
        raise _error("Native review requires exactly one checkpoint marker.")
    return request["prompt"], markers[0]


class _AppServer:
    """Bound stdio RPC, native notifications and process cleanup."""

    def __init__(self, command, environment, cwd, control, output_file):
        self.command = command
        self.environment = environment
        self.cwd = cwd
        self.control = control
        self.output_file = output_file
        self.process = None
        self.output = None
        self.log_file = None
        self.started = time.monotonic()
        self.last_activity = self.started
        self.bytes = 0
        self.lines = 0
        self.log = []
        self.sequence = 0
        self.notifications = []
        self.usage = TokenUsage()
        self.baselines = {}
        self.totals = {}
        self.usage_by_turn = {}
        self.fresh = set()
        self.turns = {}
        self.models = {}
        self.children = set()
        self.sandboxes = {}
        self.messages = {}
        self.active = None
        self.exited_at = None
        context = execution_context("codex", capabilities=["long-command"])
        self.idle = numeric_limit("agent.stdout-idle", "idle", context, expected_unit="seconds")

    def __enter__(self):
        try:
            self.process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                cwd=self.cwd,
                env=self.environment,
                start_new_session=os.name != "nt",
            )
            self.output = ProcessOutput(self.process, max_line_bytes=1024 * 1024)
            if self.control and self.control.on_process_started:
                self.control.on_process_started()
            if self.output_file:
                self.log_file = open(self.output_file, "a", encoding="utf-8")
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *unused):
        if self.process:
            if os.name != "nt" or self.process.poll() is None:
                self.stop()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.stop(hard=True)
                self.process.wait(timeout=2)
            if os.name != "nt":
                self.stop(hard=True)
            if self.process.stdin:
                self.process.stdin.close()
        if self.output:
            self.output.close()
        if self.log_file:
            self.log_file.close()

    def stop(self, *, hard=False):
        try:
            if os.name != "nt":
                os.killpg(self.process.pid, signal.SIGKILL if hard else signal.SIGTERM)
            elif not hard:
                self.process.terminate()
            else:
                self.process.kill()
        except ProcessLookupError:
            pass

    def write(self, value):
        try:
            self.process.stdin.write(json.dumps(value) + "\n")
            self.process.stdin.flush()
        except (OSError, ValueError) as exc:
            raise _error("Codex app-server input closed.", "execution_failed") from exc

    def record(self, value):
        line = json.dumps(value)
        self.log.append(line)
        if self.log_file:
            self.log_file.write(line + "\n")
            self.log_file.flush()

    def read(self):
        while True:
            now = time.monotonic()
            duration = self.control.max_duration_seconds if self.control else None
            if duration and now - self.started >= duration:
                raise _error(
                    "Codex native execution exceeded its duration limit.", "execution_limit"
                )
            if now - self.last_activity >= self.idle:
                raise _error("Codex app-server stopped producing activity.", "timeout")
            try:
                line = self.output.readline(timeout=0.1)
            except Empty:
                if self.process.poll() is not None:
                    if self.exited_at is None:
                        self.exited_at = now
                    # Drain any final records already buffered by the reader;
                    # descendants may otherwise hold these pipes open forever.
                    if now - self.exited_at >= 0.25:
                        raise _error(
                            "Codex app-server exited before native completion.",
                            "incomplete_response",
                        )
                continue
            if line is None:
                raise _error(
                    "Codex app-server closed before native completion.", "incomplete_response"
                )
            self.last_activity = time.monotonic()
            self.bytes += len(line.encode("utf-8"))
            self.lines += 1
            byte_limit = (
                self.control.max_output_bytes if self.control else None
            ) or 16 * 1024 * 1024
            line_limit = (self.control.max_output_lines if self.control else None) or 32768
            if self.bytes > byte_limit or self.lines > line_limit:
                raise _error("Codex native execution exceeded its output limit.", "execution_limit")
            try:
                event = json.loads(line)
            except (ValueError, RecursionError) as exc:
                raise _error(
                    "Codex app-server returned malformed JSON.", "invalid_evidence"
                ) from exc
            if not isinstance(event, dict):
                raise _error("Codex app-server returned an invalid RPC record.", "invalid_evidence")
            if "method" in event:
                # Config RPC responses may contain credentials; never persist them.
                self.record(event)
                if "id" in event:
                    # No interactive permissions, external tools or additional reviews.
                    self.write(
                        {
                            "id": event["id"],
                            "error": {
                                "code": -32601,
                                "message": "CAFE does not grant interactive host operations.",
                            },
                        }
                    )
                    raise _error("Codex requested an unsupported interactive host operation.")
                self.observe(event)
            return event

    def observe(self, event):
        params = event.get("params", {})
        if not isinstance(params, dict):
            raise _error("Codex returned malformed notification parameters.", "invalid_evidence")
        thread = params.get("threadId")
        if event["method"] == "model/rerouted" and thread in self.models:
            if params.get("toModel") != self.models[thread]:
                raise _error("Codex rerouted the selected native model.", "model_mismatch")
        if event["method"] == "thread/settings/updated" and thread in self.models:
            settings = params.get("threadSettings", {})
            if (
                settings.get("model") != self.models[thread]
                or settings.get("approvalPolicy") != "never"
                or (
                    thread in self.sandboxes
                    and settings.get("sandboxPolicy") != self.sandboxes[thread]
                )
            ):
                raise _error(
                    "Codex changed the verified native thread settings.", "invalid_evidence"
                )
        if event["method"] == "turn/completed" and thread in self.models:
            terminal = params.get("turn", {})
            key = thread, terminal.get("id")
            fingerprint = terminal.get("status"), terminal.get("error")
            if key in self.turns and self.turns[key] != fingerprint:
                raise _error("Codex returned conflicting terminal states.", "invalid_evidence")
            self.turns[key] = fingerprint
        if event["method"] == "item/completed" and thread in self.models:
            item = params.get("item", {})
            if item.get("type") == "collabAgentToolCall":
                raise _error("Codex attempted undeclared native delegation.", "invalid_evidence")
            if item.get("type") == "agentMessage":
                key = thread, params.get("turnId"), item.get("id")
                if key in self.messages and self.messages[key] != item:
                    raise _error("Codex returned conflicting native messages.", "invalid_evidence")
                self.messages[key] = item
        if event["method"] == "thread/tokenUsage/updated":
            if thread not in self.baselines:
                return
            total = _counters(params.get("tokenUsage", {}).get("total"))
            previous = self.totals.get(thread)
            if previous and any(total[k] < previous[k] for k in total.keys() & previous.keys()):
                raise _error("Codex native token counters decreased.", "invalid_evidence")
            self.totals[thread] = total
            self.usage_by_turn[(thread, params.get("turnId"))] = total
        if event["method"] in {"item/completed", "turn/completed"}:
            self.notifications.append(event)

    def request(self, method, params):
        self.sequence += 1
        identifier = self.sequence
        self.write({"id": identifier, "method": method, "params": params})
        while True:
            event = self.read()
            if event.get("id") != identifier or "method" in event:
                continue
            if "error" in event:
                detail = sanitize_error_excerpt(json.dumps(event["error"]))
                error = _error(f"Codex {method} failed: {detail}")
                error.native_session_recoverable = method == "thread/resume" and any(
                    marker in detail.lower()
                    for marker in (
                        "no rollout found",
                        "session not found",
                        "conversation does not exist",
                    )
                )
                raise error
            if not isinstance(event.get("result"), dict):
                raise _error("Codex app-server omitted a native RPC result.", "invalid_evidence")
            return event["result"]

    def turn(self, thread, model, prompt, *, read_only=False, output_schema=None):
        previous = self.totals.get(thread, self.baselines.get(thread))
        account = prepare_cost_accounting("codex", self.environment)
        params = {
            "threadId": thread,
            "model": model,
            "approvalPolicy": "never",
            "input": [{"type": "text", "text": prompt, "text_elements": []}],
        }
        if read_only:
            params["sandboxPolicy"] = {"type": "readOnly", "networkAccess": False}
        elif self.sandboxes.get(thread) == {"type": "dangerFullAccess"}:
            params["sandboxPolicy"] = {"type": "dangerFullAccess"}
        if output_schema:
            params["outputSchema"] = output_schema
        started = time.monotonic()
        self.notifications = []
        turn_id = None
        status = None
        messages = {}
        try:
            result = self.request("turn/start", params)
            turn_id = _identity(result.get("turn", {}).get("id"))
            self.active = (thread, turn_id)
            while status is None:
                while self.notifications:
                    event = self.notifications.pop(0)
                    data = event["params"]
                    if data.get("threadId") != thread:
                        continue
                    if event["method"] == "item/completed" and data.get("turnId") == turn_id:
                        item = data.get("item", {})
                        if item.get("type") == "agentMessage" and item.get("phase") in {
                            None,
                            "final_answer",
                        }:
                            identifier = _identity(item.get("id"))
                            text = item.get("text")
                            if not isinstance(text, str):
                                raise _error(
                                    "Codex returned an invalid assistant message.",
                                    "invalid_evidence",
                                )
                            if identifier in messages and messages[identifier] != text:
                                raise _error(
                                    "Codex returned conflicting assistant messages.",
                                    "invalid_evidence",
                                )
                            messages[identifier] = text
                    elif (
                        event["method"] == "turn/completed"
                        and data.get("turn", {}).get("id") == turn_id
                    ):
                        terminal = data["turn"]
                        status = terminal.get("status")
                        if status != "completed" or terminal.get("error"):
                            error = terminal.get("error") or {}
                            code = error.get("codexErrorInfo")
                            kind = (
                                "rate_limit"
                                if isinstance(code, str)
                                and code in {"rateLimitExceeded", "usageLimitExceeded"}
                                else "execution_failed"
                            )
                            raise _error(
                                "Codex native turn did not complete: "
                                + sanitize_error_excerpt(json.dumps(error)),
                                kind,
                            )
                if status is None:
                    self.read()
            if not messages:
                raise _error("Codex completed without an assistant result.", "incomplete_response")
            return list(messages.values())[-1]
        finally:
            total = self.usage_by_turn.get((thread, turn_id))
            values = {}
            if previous is not None and total is not None:
                if any(total[k] < previous[k] for k in total.keys() & previous.keys()):
                    raise _error("Codex usage does not cover this native turn.", "invalid_evidence")
                values = {
                    k: v - previous.get(k, 0)
                    for k, v in total.items()
                    if k in previous or thread in self.fresh
                }
            usage = account(
                TokenUsage(**values, duration_ms=int((time.monotonic() - started) * 1000)),
                model,
                complete=status == "completed",
            )
            usage.turn_usages = [
                {
                    "turn": len(self.usage.turn_usages) + 1,
                    "thread_id": thread,
                    "turn_id": turn_id,
                    "model": model,
                    **values,
                }
            ]
            self.usage = TokenUsage(
                **merge_token_usage_stats(self.usage.model_dump(exclude_unset=True), usage)
            )
            self.active = None
            self.fresh.discard(thread)
            self.baselines[thread] = total


def _toml_configuration(value):
    """Native config/read serializes absent typed options as JSON null.

    TOML has no null, and Codex's RPC conversion turns null into an empty string.
    Omit absent fields when round-tripping effective MCP transport definitions.
    """
    if isinstance(value, dict):
        return {key: _toml_configuration(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_toml_configuration(item) for item in value]
    return value


def _child_configuration(server, cwd):
    """Disable external write-capable tools, including every effective MCP server."""
    settings = server.request("config/read", {"cwd": str(cwd), "includeLayers": False})["config"]
    mcp = settings.get("mcp_servers", {})
    if not isinstance(mcp, dict) or len(mcp) > 1024:
        raise _error("Codex MCP configuration cannot be bounded for native review.")
    requirements = server.request("configRequirements/read", {}).get("requirements")
    if requirements is not None:
        if not isinstance(requirements, dict):
            raise _error("Codex managed requirements are invalid.")
        features = requirements.get("featureRequirements") or {}
        if not isinstance(features, dict) or any(
            features.get(name) is True for name in _DISABLED_FEATURES
        ):
            raise _error("Codex managed requirements force unsafe native reviewer features.")
    config = {"features." + name: False for name in _DISABLED_FEATURES}
    config.update(
        {
            "agents.enabled": False,
            "notify": [],
            "orchestrator.mcp.enabled": False,
            "web_search": "disabled",
        }
    )
    # RPC override keys are split on periods, without TOML quoted-key parsing.
    # Override the complete effective table so literal server names are preserved.
    servers = {}
    for name, settings in mcp.items():
        if not isinstance(name, str) or len(name) > 512 or not isinstance(settings, dict):
            raise _error("Codex MCP server configuration is invalid.")
        servers[name] = {**_toml_configuration(settings), "enabled": False}
    config["mcp_servers"] = servers
    return config


def _verified_thread(result, *, model, cwd, parent=None, enforcement="sandbox"):
    thread = result.get("thread", {})
    identifier = _identity(thread.get("id"))
    if result.get("model") != model or Path(result.get("cwd", "")).resolve() != cwd:
        raise _error("Codex native thread differs from the selected model or workspace.")
    if result.get("approvalPolicy") != "never":
        raise _error("Codex native thread permits interactive escalation.")
    if enforcement == "instruction_only" and result.get("sandbox") != {"type": "dangerFullAccess"}:
        raise _error("Codex did not establish the confirmed execution without an OS sandbox.")
    if (
        enforcement == "sandbox"
        and parent is None
        and result.get("sandbox", {}).get("type") != "workspaceWrite"
    ):
        raise _error("Codex did not establish a writable development parent.")
    if parent is not None and (
        identifier == parent
        or thread.get("forkedFromId") != parent
        or (
            enforcement == "sandbox"
            and result.get("sandbox") != {"type": "readOnly", "networkAccess": False}
        )
    ):
        raise _error("Codex did not establish the confirmed independent native fork.")
    return identifier


_REVIEW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "severity": {"type": "string", "enum": ["blocking", "nonblocking"]},
                    "detail": {"type": "string"},
                },
                "required": ["severity", "detail"],
            },
        },
        "targeted_tests": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["findings", "targeted_tests"],
}


def execute_native_review(
    executor,
    command,
    prompt,
    *,
    environment,
    working_directory=None,
    allowed_directories=None,
    execution_control=None,
    streaming_output_file=None,
):
    """Run development, confirmed native review and exact parent continuation."""
    configuration = validate_configuration(executor.config)
    enforcement = review_read_only_enforcement(configuration)
    unconfined = enforcement == "instruction_only"
    cwd = Path(working_directory or command[command.index("-C") + 1]).resolve()
    baseline = None
    if executor.config.session_id:
        from cafe.agents.cli.codex_usage import _baseline

        try:
            home = Path(environment.get("CODEX_HOME") or Path.home() / ".codex")
            baseline = _baseline(home, executor.config.session_id)[-1]
        except (OSError, ValueError, TypeError, RecursionError):
            pass  # Continue the exact session, but do not invent attributable tokens.
    server = _AppServer(command, environment, cwd, execution_control, streaming_output_file)
    parent = None
    observations = []
    try:
        with server:
            server.request(
                "initialize", {"clientInfo": {"name": "cafe-native-review", "version": "1.0"}}
            )
            server.write({"method": "initialized"})
            _child_configuration(server, cwd)
            params = {
                "cwd": str(cwd),
                "model": executor.config.model,
                "sandbox": "danger-full-access" if unconfined else "workspace-write",
                "approvalPolicy": "never",
                "developerInstructions": "This invocation uses CAFE's native fork review protocol. "
                "After implementation, checks and the scope checkpoint, end the turn with "
                '{"cafe_native_review":{"prompt":"... CAFE_REVIEW_CHECKPOINT:<receipt_id>"}}. '
                "Do not launch another reviewer CLI or call spawn_agent. "
                "On the host continuation, preserve reviewed content and record the exact "
                "independent review result before the declared handoff.",
            }
            if allowed_directories:
                from cafe.agents.cli.codex import CodexCLI

                roots = CodexCLI(executor.config)._expand_initial_allowed_directories(
                    allowed_directories, cwd
                )
                params["config"] = {
                    "sandbox_workspace_write.writable_roots": [
                        str((cwd / root).resolve()) for root in roots
                    ]
                }
            if executor.config.session_id:
                params.update(threadId=executor.config.session_id, excludeTurns=True)
                opened = server.request("thread/resume", params)
            else:
                opened = server.request("thread/start", params)
                baseline = {"input_tokens": 0, "output_tokens": 0}
            parent = _verified_thread(
                opened, model=executor.config.model, cwd=cwd, enforcement=enforcement
            )
            if executor.config.session_id and parent != executor.config.session_id:
                raise _error("Codex resumed a different native session.", "session_mismatch")
            if params.get("threadId") is None:
                server.fresh.add(parent)
            server.baselines[parent] = baseline
            server.models[parent] = opened["model"]
            if unconfined:
                server.sandboxes[parent] = opened["sandbox"]
            executor.config.session_id = parent
            # Normalized identity comes from the actual RPC response, not model text.
            server.record({"type": "thread.started", "thread_id": parent})
            context_prompt = "Your exact CAFE parent thread ID is " + parent + ".\n" + prompt
            response = server.turn(parent, opened["model"], context_prompt)
            request = _review_request(response)
            if request is not None:
                review_prompt, receipt = request
                observed_at = datetime.now(timezone.utc).isoformat()
                child_config = _child_configuration(server, cwd)
                fork = server.request(
                    "thread/fork",
                    {
                        "threadId": parent,
                        "cwd": str(cwd),
                        "model": configuration["model"],
                        "sandbox": "danger-full-access" if unconfined else "read-only",
                        "approvalPolicy": "never",
                        "ephemeral": True,
                        "excludeTurns": True,
                        "developerInstructions": _PROMPT
                        + (
                            " OS sandboxing is disabled. Inspection-only is a role instruction; "
                            "do not describe it as enforced filesystem protection."
                            if unconfined
                            else ""
                        ),
                        "config": child_config,
                    },
                )
                child = _verified_thread(
                    fork,
                    model=configuration["model"],
                    cwd=cwd,
                    parent=parent,
                    enforcement=enforcement,
                )
                server.baselines[child] = server.totals.get(parent)
                server.models[child] = fork["model"]
                server.children.add(child)
                server.sandboxes[child] = fork["sandbox"]
                result = server.turn(
                    child,
                    fork["model"],
                    _PROMPT + "\n" + review_prompt,
                    read_only=not unconfined,
                    output_schema=_REVIEW_SCHEMA,
                )
                conclusion = _conclusion(result)
                if not conclusion:
                    raise _error("The native Codex reviewer omitted an unambiguous conclusion.")
                observation = {
                    "reviewer_id": child,
                    "receipt_id": receipt,
                    "configuration": configuration,
                    "observed_at": observed_at,
                    "terminal": "result",
                    "exit_status": 0,
                    "read_only_enforcement": enforcement,
                    "sandbox_enabled": not unconfined,
                    "parent_sandbox": opened["sandbox"],
                    "reviewer_sandbox": fork["sandbox"],
                    **conclusion,
                }
                observations.append(observation)
                server.record(
                    {
                        "type": "cafe.codex.native_fork_review",
                        "parent_id": parent,
                        "sandbox": fork["sandbox"],
                        "model": fork["model"],
                        **observation,
                    }
                )
                continuation = (
                    "CAFE's independent native fork completed under the confirmed "
                    "inspection-only policy. "
                    "Preserve reviewed content. "
                    "Record the exact conclusion below and its reviewer_id in native_review.json "
                    "with your checkpoint and then the declared handoff. "
                    "If blocking findings exist, "
                    "retain them and use the declared self-loop; "
                    "do not fix or request review again "
                    "inside this continuation. Parent ID: "
                    + parent
                    + ".\n"
                    + json.dumps(observation)
                )
                response = server.turn(parent, opened["model"], continuation)
                if _review_request(response) is not None:
                    raise _error("A second native review requires the declared workflow self-loop.")
            usage = server.usage
            executor._accumulate_usage(usage)
            return AgentResponse(
                usage_accounted=True,
                response=response,
                token_usage=usage,
                usage_available=any(
                    "input_tokens" in turn and "output_tokens" in turn for turn in usage.turn_usages
                ),
                streaming_log=server.log,
                native_review_observations=observations,
                model=opened["model"],
                session_id=parent,
                transport_result=TransportResult(
                    observed_session_id=parent,
                    reported_model=opened["model"],
                    completed=True,
                    usage=usage,
                    returncode=0,
                ),
            )
    except BaseException as exc:
        from cafe.agents.executor import AgentExecutionError

        if not isinstance(exc, AgentExecutionError):
            if isinstance(exc, FileNotFoundError):
                error = _error("Codex CLI was not found.", "cli_not_found")
            elif isinstance(exc, Exception):
                error = _error(
                    "Codex native review transport failed: " + type(exc).__name__,
                    "execution_failed",
                )
            else:
                raise
        else:
            error = exc
        if not hasattr(error, "native_session_recoverable"):
            error.native_session_recoverable = False
        error.cli_command_args = command[1:]
        error.accounting_usage = server.usage
        error.transport_result = TransportResult(
            observed_session_id=parent,
            completed=False,
            usage=server.usage,
            failure_code=error.error_type,
        )
        executor._accumulate_usage(server.usage)
        raise error from (exc if exc is not error else None)
