"""Claude CLI tool implementation."""

import json
import logging
import math
import os
import re
import stat
import uuid
from pathlib import Path
from typing import List, Optional, Tuple

from cafe.agents.cli.abstract import AbstractCLI
from cafe.agents.transport_types import TransportResult, _validated_evidence_scalar
from cafe.core.types import PermissionDenial, TokenUsage
from cafe.utils.git_utils import get_git_toplevel

logger = logging.getLogger(__name__)


class ClaudeCLI(AbstractCLI):
    """Concrete implementation of Claude CLI tool."""

    read_only_operations = frozenset({"open_interactive_session", "run_one_shot"})

    def apply_read_only(self, command: List[str], operation: str) -> List[str]:
        self.require_read_only(operation)
        # --tools selects available built-in model tools; --allowed-tools alone
        # only approves them. Claude Code 2.1.284 TUI !touch still wrote a scratch
        # file despite these restrictions and plan mode. Native UI/permissions,
        # integrations/subprocesses and IPC are not confined; provider-owned
        # session/history persistence continues inside or outside the repository.
        # Separate the positional interactive prompt before interpreting options:
        # prompt/model values may themselves look like permission option names.
        source = command[1:]
        positional = []
        if operation == "open_interactive_session" and len(source) % 2:
            positional = ["--", source[-1]]
            source = source[:-1]
        native = []
        index = 0
        while index < len(source):
            option = source[index]
            if option in {"--tools", "--allowed-tools", "--disallowed-tools", "--permission-mode"}:
                index += 2  # Replace only conflicting CAFE-built option pairs.
            elif option in {"-p", "--resume", "--model", "--output-format", "--add-dir"}:
                native.extend(source[index : index + 2])
                index += 2
            else:
                native.append(option)
                index += 1
        native.extend(positional)
        options = [
            "--tools",
            "Read,Glob,Grep",
            "--allowed-tools",
            "Read,Glob,Grep",
            "--disallowed-tools",
            "Bash,Edit,Write,NotebookEdit",
            "--permission-mode",
            "plan",
        ]
        return [command[0], *options, *native]

    def prepare_interactive_accounting(self, command, environment):
        """Read only native records appended during this terminal invocation.

        Native assistant usage is partial evidence: it does not certify billing,
        subagent coverage, or sessions the user switches to inside the terminal.
        The caller retains that coverage gap even when counters are available.
        """
        session = self.config.session_id or str(uuid.uuid4())
        try:
            uuid.UUID(session)
        except (ValueError, TypeError, AttributeError):
            return command, None
        if not self.config.session_id:
            command = command[:1] + ["--session-id", session] + command[1:]
        native_root = Path(environment.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
        project = re.sub(r"[^a-zA-Z0-9]", "-", str(Path.cwd()))
        journal = native_root / "projects" / project / (session + ".jsonl")
        # Bounded scans and line reads also bound ephemeral identity storage.
        byte_limit, line_limit, record_limit = 16 * 1024 * 1024, 256 * 1024, 65536

        def read_records(offset=0, expected=None, *, strict=True):
            descriptor = os.open(journal, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as handle:
                info = os.fstat(handle.fileno())
                identity = info.st_dev, info.st_ino
                if not stat.S_ISREG(info.st_mode) or (expected and identity != expected):
                    raise ValueError("native accounting source changed")
                if info.st_size < offset or (strict and info.st_size - offset > byte_limit):
                    raise ValueError("native accounting source exceeds bound")
                handle.seek(offset)
                records = []
                scanned = 0
                while handle.tell() < min(info.st_size, offset + byte_limit):
                    line = handle.readline(line_limit + 1)
                    scanned += 1
                    try:
                        if (
                            len(line) > line_limit
                            or not line.endswith(b"\n")
                            or scanned > record_limit
                        ):
                            raise ValueError("native accounting record exceeds bound")
                        data = json.loads(line)
                        if not isinstance(data, dict):
                            raise ValueError("invalid native accounting record")
                        # Drop all content immediately; retain only accounting evidence.
                        message = data.get("message")
                        if data.get("type") == "assistant" and isinstance(message, dict):
                            if data.get("sessionId") != session:
                                raise ValueError("native accounting session mismatch")
                            identity_key = _validated_evidence_scalar(message.get("id"))
                            if identity_key is None:
                                raise ValueError("native accounting message identity missing")
                            records.append(
                                (identity_key, message.get("model"), message.get("usage"))
                            )
                    except ValueError:
                        if strict:
                            raise
                        break  # Keep verified prefix subtotals, with incomplete coverage.
                return identity, info.st_size, records

        try:
            inode, offset, history = read_records()
            historical = {record[0] for record in history}
        except FileNotFoundError:
            if self.config.session_id:
                # Resume may reconstruct history; without a baseline it cannot
                # be safely attributed to this invocation.
                return command, None
            inode, offset, historical = None, 0, set()
        except (OSError, ValueError):
            return command, None

        def collect():
            try:
                _inode, _end, records = read_records(offset, inode, strict=False)
                unique = {}
                for identity, model, usage in records:
                    if identity in historical:
                        continue
                    try:
                        reported = _validated_evidence_scalar(model)
                    except ValueError:
                        reported = None
                    previous = unique.get(identity)
                    if previous and previous[0] != reported:
                        break  # Conflicting evidence cannot certify more usage.
                    if not isinstance(usage, dict):
                        # A model without usage must remain an explicit gap.
                        if previous is None:
                            unique[identity] = (reported, None)
                        continue
                    try:
                        _text, parsed, _denials = self.parse_response(
                            [json.dumps({"usage": usage})]
                        )
                    except (ValueError, TypeError):
                        break
                    known = parsed.model_dump(include=parsed.model_fields_set - {"turn_usages"})
                    if any(
                        isinstance(value, bool)
                        or not isinstance(value, (int, float))
                        or not math.isfinite(value)
                        or value < 0
                        for value in known.values()
                    ):
                        break
                    # Repeated content blocks/cumulative snapshots of one message
                    # are one API request. Preserve the largest observed counters.
                    if previous and previous[1] is not None:
                        prior = previous[1].model_dump(include=previous[1].model_fields_set)
                        known = {
                            key: max(prior.get(key, value), value) for key, value in known.items()
                        } | {key: value for key, value in prior.items() if key not in known}
                    unique[identity] = reported, TokenUsage(**known) if known else None
                return tuple(
                    TransportResult(reported_model=model, usage=usage)
                    for model, usage in unique.values()
                )
            except (OSError, ValueError, TypeError):
                return ()

        return command, collect

    def build_command(
        self,
        prompt: str,
        allowed_tools: Optional[List[str]] = None,
        allowed_directories: Optional[List[str]] = None,
    ) -> List[str]:
        """Build Claude CLI command line arguments.

        Parameter order: claude -> --resume -> -p -> --model -> --allowed-tools -> --output-format -> --add-dir

        Args:
            prompt: Prompt text
            allowed_tools: List of allowed tools (already converted format)
            allowed_directories: List of allowed directories

        Returns:
            Complete command line argument list
        """
        cmd = ["claude"]

        # 1. If has session_id, add --resume parameter (must be before -p)
        if self.config.session_id:
            cmd.extend(["--resume", self.config.session_id])

        # 2. Add -p parameter (always at front, except --resume)
        cmd.extend(["-p", prompt])

        # 3. If has model, add --model parameter (must be after -p)
        if self.config.model:
            cmd.extend(["--model", self.config.model])

        # 4. If has allowed_tools, add --allowed-tools parameter
        if allowed_tools:
            tools_arg_value = ",".join(allowed_tools)
            cmd.extend(["--allowed-tools", tools_arg_value])

        # 5. Add output format parameter
        cmd.extend(self.get_output_format())

        # 6. If has allowed_directories, add --add-dir parameter
        if allowed_directories:
            cmd = self.add_directories(cmd, allowed_directories)

        return cmd

    def parse_response(
        self,
        output_lines: List[str],
        streaming_log: Optional[List[str]] = None,
    ) -> Tuple[str, TokenUsage, List[PermissionDenial]]:
        """Parse Claude CLI's stream-json output.

        Args:
            output_lines: List of lines from CLI output
            streaming_log: Streaming output log (optional, not used here)

        Returns:
            (response, token_usage, permission_denials) tuple
        """
        response_text = ""
        token_usage = TokenUsage()
        permission_denials = []

        for line in output_lines:
            try:
                data = json.loads(line.strip())
                if not isinstance(data, dict):
                    continue

                # Extract content (new format: message.content[] or old format: content)
                message = data.get("message")
                if isinstance(message, dict) and isinstance(message.get("content"), list):
                    for content_block in message["content"]:
                        if not isinstance(content_block, dict):
                            continue
                        if content_block.get("type") == "text":
                            response_text = content_block.get("text", "")
                elif "content" in data:
                    response_text = data["content"]

                # Extract token usage
                if "usage" in data:
                    usage_data = data["usage"]
                    if isinstance(usage_data, dict):
                        token_usage = TokenUsage(**{
                            key: value for key, value in usage_data.items()
                            if key in TokenUsage.model_fields and key not in {"turn_usages", "cost_records"}
                        })

                if "total_cost_usd" in data:
                    token_usage.total_cost_usd = data["total_cost_usd"]

                # Extract permission denials
                if "permission_denials" in data and data["permission_denials"]:
                    for denial_data in data["permission_denials"]:
                        permission_denials.append(
                            PermissionDenial(
                                tool_name=denial_data["tool_name"],
                                tool_input=denial_data["tool_input"]
                            )
                        )

            except json.JSONDecodeError:
                # Non-JSON line, ignore
                continue

        return response_text, token_usage, permission_denials

    def translate_allowed_tools(self, tools: List[str]) -> List[str]:
        """Convert tool names and paths to Claude permission-rule format.

        Args:
            tools: List of tool names (lowercase format, e.g. ["read", "write(/path)"])

        Returns:
            List of converted tool names (e.g. ["Read", "Write(//repo/.cafe/config.yaml)"])
        """
        processed_tools = []
        tool_name_map = {
            "bash": "Bash",
            "read": "Read",
            "write": "Write",
            "edit": "Edit",
            "grep": "Grep",
            "glob": "Glob",
            "ls": "LS",
            "webfetch": "WebFetch",
            "web_fetch": "WebFetch",
            "websearch": "WebSearch",
            "web_search": "WebSearch",
        }

        for tool in tools:
            # Handle tools with paths or commands (e.g. write(/path) or bash(git status))
            if "(" in tool and ")" in tool:
                tool_name = tool.split("(")[0].lower()
                path_or_cmd = tool.split("(")[1].rstrip(")")
                display_tool_name = tool_name_map.get(tool_name, tool.split("(")[0])

                # Determine if it's a path or command
                # If tool_name is bash, treat as command, don't convert path format
                if tool_name == "bash":
                    # Command parameter, use directly, don't add / prefix
                    processed_tool = f"{display_tool_name}({path_or_cmd})"
                else:
                    permission_path = self._to_permission_path(path_or_cmd)
                    processed_tool = f"{display_tool_name}({permission_path})"
            else:
                # Tool has no path parameter, normalize known Claude tool names.
                processed_tool = tool_name_map.get(tool.lower(), tool)

            # Remove duplicates
            if processed_tool not in processed_tools:
                processed_tools.append(processed_tool)

        return processed_tools

    @staticmethod
    def _to_permission_path(path: str) -> str:
        """Return a cwd-independent Claude permission path.

        Claude uses ``//path`` for absolute filesystem permission rules. CAFE's
        historical single-leading-slash form is repository-root relative, while
        unprefixed paths are also repository relative. Resolve both against the
        active checkout so a resumed agent can change directories without
        invalidating its workflow-artifact permissions.
        """
        if path.startswith("//"):
            return path

        try:
            checkout_root = get_git_toplevel().resolve()
        except (ValueError, OSError):
            checkout_root = Path.cwd().resolve()

        path_obj = Path(path)
        if path_obj.is_absolute():
            try:
                path_obj.relative_to(checkout_root)
                absolute_path = path_obj
            except ValueError:
                # A single leading slash is CAFE's legacy repository-root form.
                absolute_path = checkout_root / path.lstrip("/")
        else:
            absolute_path = checkout_root / path.removeprefix("./")

        return "/" + str(absolute_path.resolve())

    def add_directories(self, cmd: List[str], directories: List[str]) -> List[str]:
        """Add canonical allowed directories to command line arguments.

        Claude CLI evaluates its write sandbox against canonical filesystem
        paths.  Passing a relative directory such as ``.cafe`` works for
        reads, but can reject writes from a nested worktree because the tool
        resolves the target path before comparing it to ``--add-dir``.

        Args:
            cmd: Current command line arguments
            directories: List of directories

        Returns:
            Updated command line arguments
        """
        for directory in directories:
            cmd.extend(["--add-dir", str(Path(directory).expanduser().resolve())])
        return cmd

    def get_output_format(self) -> List[str]:
        """Get Claude CLI's output format parameters.

        Returns:
            Output format related command line parameters
        """
        return ["--output-format", "stream-json", "--verbose"]

    def extract_session_id(self, output_lines: List[str]) -> Optional[str]:
        """Extract session ID from output.

        Args:
            output_lines: List of lines from CLI output

        Returns:
            Session ID, or None if not found
        """
        for line in output_lines:
            try:
                data = json.loads(line.strip())
                if isinstance(data, dict) and "session_id" in data:
                    return data["session_id"]
            except json.JSONDecodeError:
                continue
        return None

    @property
    def event_driver_conforming(self) -> bool:
        return True

    def build_event_driver_command(
        self,
        prompt: str,
        allowed_tools: Optional[List[str]] = None,
        allowed_directories: Optional[List[str]] = None,
    ) -> List[str]:
        command = super().build_event_driver_command(
            prompt, allowed_tools, allowed_directories
        )
        command.append("--include-partial-messages")
        return command

    def conversation_reported_models(self, record):
        models = super().conversation_reported_models(record)
        if record.get("type") == "stream_event":
            event = record.get("event")
            if isinstance(event, dict) and event.get("type") == "message_start":
                message = event.get("message")
                if isinstance(message, dict) and message.get("model") is not None:
                    models.append(message["model"])
        return models

    conversation_session_field = "session_id"

    conversation_operations = frozenset({
        "acquire_session", "deliver_to_exact_session", "open_interactive_session", "run_one_shot",
    })
    conversation_session_operations = conversation_operations - {"open_interactive_session"}
    conversation_model_operations = conversation_operations - {"open_interactive_session"}
    conversation_usage_operations = conversation_operations - {"open_interactive_session"}
    conversation_acceptance_operations = frozenset({"deliver_to_exact_session"})

    def conversation_identity_record(self, record):
        return record.get("type") == "system" and record.get("subtype") == "init"

    def extract_event_driver_session(self, records) -> Optional[str]:
        return self._verified_event_driver_session(
            records,
            matches=lambda record: record.get("type") == "system"
            and record.get("subtype") == "init",
            field="session_id",
        )

    def accepts_event_driver_callback(self, records, *, session_id: str, event_id: str) -> bool:
        return self._verified_event_driver_acceptance(
            records,
            session_matches=lambda record: record.get("type") == "system"
            and record.get("subtype") == "init",
            acceptance_matches=lambda record: record.get("type") == "stream_event"
            and isinstance(record.get("event"), dict)
            and record["event"].get("type") == "message_start",
            session_field="session_id",
            session_id=session_id,
            event_id=event_id,
        )

    def create_session(self) -> str:
        """Claude sessions are created by the real prompt execution."""
        return ""
    def project_native_review(self, command: List[str]) -> List[str]:
        configuration = self.config.native_review_configuration
        if configuration is None:
            return command
        if (configuration.get("cli") != "claude" or configuration.get("read_only") is not True or
                configuration.get("checkpoint_interface") != "parent_command"):
            raise ValueError("unsupported native reviewer configuration")
        behavior = configuration.get("model_behavior")
        if behavior == "inherits_parent" and configuration.get("model") != self.config.model:
            raise ValueError("inherited reviewer model differs from the effective parent")
        if behavior not in {"inherits_parent", "independent_override"}:
            raise ValueError("unsupported native reviewer model behavior")
        agent = {"description": "Independent read-only implementation reviewer",
                 "prompt": "Review correctness, completeness, unnecessary changes, architecture and tests. Never modify files or workflow state. Return explicit blocking/nonblocking findings.",
                 "tools": ["Read", "Glob", "Grep"],
                 "model": "inherit" if behavior == "inherits_parent" else configuration["model"]}
        return [*command, "--agents", json.dumps({"cafe_reviewer": agent})]

    def native_review_observations(self, output_lines: List[str], *, observed_at=None) -> List[dict]:
        """Retain bounded protocol metadata, never reviewer text or tool payloads."""
        from datetime import datetime, timezone
        invocations = {}
        for line in output_lines:
            try:
                record = json.loads(line)
            except (ValueError, TypeError):
                continue
            if not isinstance(record, dict) or not isinstance(record.get("message", {}), dict):
                continue
            content = record.get("message", {}).get("content", [])
            if not isinstance(content, list):
                continue
            for item in content:
                if not isinstance(item, dict):
                    continue
                args = item.get("input", {})
                if (item.get("type") == "tool_use" and item.get("name") in {"Agent", "Task"}
                        and isinstance(args, dict) and args.get("subagent_type") == "cafe_reviewer"):
                    marker = re.search(r"CAFE_REVIEW_CHECKPOINT:([A-Za-z0-9-]+)", str(args.get("prompt", "")))
                    invocation_id = item.get("id")
                    if not isinstance(invocation_id, str) or len(invocations) >= 16:
                        continue
                    invocations[invocation_id] = {
                        "reviewer_id": invocation_id, "receipt_id": marker.group(1) if marker else None,
                        "configuration": self.config.native_review_configuration,
                        "observed_at": (observed_at.get(id(line)) if observed_at is not None else datetime.now(timezone.utc).isoformat()),
                        "terminal": None, "exit_status": None,
                        "background": args.get("run_in_background", False)}
                if item.get("type") == "tool_result" and item.get("tool_use_id") in invocations:
                    observed = invocations[item["tool_use_id"]]
                    if not observed["background"] and not item.get("is_error", False):
                        observed.update(terminal="result", exit_status=0)
                        payload = item.get("content", "")
                        if isinstance(payload, list):
                            payload = "\n".join(v.get("text", "") for v in payload if isinstance(v, dict))
                        if isinstance(payload, str) and len(payload.encode()) <= 128 * 1024:
                            decoder = json.JSONDecoder()
                            conclusions = []
                            for match in re.finditer(r"\{", payload):
                                try:
                                    conclusion, _ = decoder.raw_decode(payload[match.start():])
                                except ValueError:
                                    continue
                                if isinstance(conclusion, dict) and set(conclusion) == {"findings", "targeted_tests"}:
                                    conclusions.append(conclusion)
                            if len(conclusions) == 1:
                                observed.update(conclusions[0])
        return list(invocations.values())
