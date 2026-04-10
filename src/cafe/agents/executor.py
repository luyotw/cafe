"""Agent executor for running AI agents."""

import json
import subprocess
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from cafe.agents.cli import AbstractCLI, ClaudeCLI, CodexCLI, CopilotCLI, CursorCLI, GeminiCLI
from cafe.core.types import AgentConfig, AgentCLI, AgentResponse, PermissionDenial, TokenUsage
from cafe.utils.git_utils import get_repo_root, to_git_ignore_path, to_relative_path


class AgentExecutionError(Exception):
    """Agent execution error."""
    
    def __init__(self, message: str, error_type: Optional[str] = None):
        super().__init__(message)
        self.error_type = error_type


class AgentExecutor:
    """Executes AI agents and handles their responses."""

    # Tool name mapping from Claude syntax to other CLIs
    # Reference: https://github.com/google-gemini/gemini-cli/blob/main/docs/tools/file-system.md
    TOOL_NAME_MAP = {
        AgentCLI.CLAUDE: {
            # Claude uses these names (standard)
            # Reference: https://gist.github.com/wong2/e0f34aac66caf890a332f7b6f9e2ba8f
            "bash": "Bash",
            "read": "Read",
            "write": "Write",
            "edit": "Edit",
            "grep": "Grep",
            "glob": "Glob",
            "ls": "LS",
            "web_fetch": "WebFetch",
            "web_search": "WebSearch",
        },
        AgentCLI.GEMINI: {
            # Gemini tool name translations
            # Reference: https://geminicli.com/docs/tools/
            "bash": "run_shell_command",
            "read": "read_file",
            "write": "write_file",
            "edit": "write_file",  # Gemini CLI doesn't support replace, use write_file instead
            "grep": "search_file_content",
            "glob": "glob",
            "ls": "list_directory",
            "web_fetch": "web_fetch",
            "web_search": "google_web_search",
        },
        AgentCLI.CURSOR: {
            # Cursor tool name translations
            # Reference: https://cursor.com/zh-Hant/docs/cli/reference/permissions
            "bash": "Shell",
            "read": "Read",
            "write": "Write",
            "edit": "Write",
            "grep": "Shell(grep)",
            "glob": "Shell(ls)",
            "ls": "Shell(ls)",
            "web_fetch": "Shell(curl)",
            "web_search": "Shell(curl)",
        },
        AgentCLI.COPILOT: {
            # GitHub Copilot CLI tool name translations
            # Reference: https://docs.github.com/en/copilot/concepts/agents/about-copilot-cli#using-the-approval-options
            "bash": "shell",
            "read": "write",  # Copilot uses 'write' for all file operations
            "write": "write",
            "edit": "write",
            "grep": "shell(grep)",
            "glob": "shell(ls)",
            "ls": "shell(ls)",
            "web_fetch": "shell(curl)",
            "web_search": "shell(curl)",
        },
        AgentCLI.CODEX: {},
    }

    def __init__(self, config: AgentConfig) -> None:
        """Initialize agent executor.

        Args:
            config: Agent configuration
        """
        self.config = config
        self._total_token_usage = TokenUsage()

    def _get_cli_strategy(self) -> AbstractCLI:
        """Get the appropriate CLI strategy based on config.

        Returns:
            CLI strategy instance for the configured CLI

        Raises:
            AgentExecutionError: If CLI is not supported
        """
        if self.config.cli == AgentCLI.CLAUDE:
            return ClaudeCLI(self.config)
        elif self.config.cli == AgentCLI.GEMINI:
            return GeminiCLI(self.config)
        elif self.config.cli == AgentCLI.CURSOR:
            return CursorCLI(self.config)
        elif self.config.cli == AgentCLI.CODEX:
            return CodexCLI(self.config)
        elif self.config.cli == AgentCLI.COPILOT:
            return CopilotCLI(self.config)
        else:
            raise AgentExecutionError(f"Unsupported agent CLI: {self.config.cli}")

    def _translate_tool_names(self, tools: Optional[List[str]]) -> Optional[List[str]]:
        """Translate tool names from Claude convention to current CLI convention.

        Args:
            tools: List of tool names in Claude convention (e.g. ["read", "edit(/path/file)"])

        Returns:
            List of tool names translated for current CLI, or None if no tools
        """
        if not tools:
            return None

        tool_map = self.TOOL_NAME_MAP.get(self.config.cli, {})
        translated = []

        for tool in tools:
            # Check if tool has parameters (e.g. "edit(/path/file)")
            if "(" in tool:
                # Extract tool name and parameters
                tool_name = tool.split("(")[0]
                tool_params = tool[len(tool_name):]  # Get "(params)"

                # Translate tool name and append parameters
                translated_name = tool_map.get(tool_name, tool_name)
                translated.append(translated_name + tool_params)
            else:
                # Simple tool name without parameters
                translated.append(tool_map.get(tool, tool))

        return translated

    def execute(
        self,
        prompt: str,
        allowed_tools: Optional[List[str]] = None,
        allowed_directories: Optional[List[str]] = None,
        streaming_output_file: Optional[str] = None
    ) -> AgentResponse:
        """Execute the agent with given prompt.

        Args:
            prompt: Prompt to send to the agent
            allowed_tools: List of allowed tools (using Claude naming convention)
            allowed_directories: List of allowed directories (e.g., [".cafe", "src"])
            streaming_output_file: Optional file path to write streaming output line-by-line

        Returns:
            AgentResponse with response text, token usage, and permission denials

        Raises:
            AgentExecutionError: If agent execution fails
        """
        # Translate tool names to the appropriate CLI convention
        translated_tools = self._translate_tool_names(allowed_tools)

        try:
            # Get CLI strategy
            cli_strategy = self._get_cli_strategy()
            # For Gemini, ensure .geminiignore file exists
            if self.config.cli == AgentCLI.GEMINI:
                cli_strategy.ensure_geminiignore()

            # For Copilot, record existing sessions before execution
            if self.config.cli == AgentCLI.COPILOT:
                cli_strategy.record_existing_sessions()

            # Translate allowed tools using CLI-specific logic
            cli_translated_tools = cli_strategy.translate_allowed_tools(translated_tools) if translated_tools else None

            # Build command using strategy
            cmd = cli_strategy.build_command(prompt, cli_translated_tools, allowed_directories)

            # Execute with streaming
            if self.config.cli == AgentCLI.COPILOT:
                # Copilot doesn't use stream-json (plain text output)
                parse_stream_json = False
            else:
                # Gemini, Claude, Cursor use stream-json format
                parse_stream_json = True

            # Execute with session recovery if session_id configured
            if self.config.session_id:
                def extract_codex_content(data: dict) -> Optional[str]:
                    if data.get("type") != "item.completed":
                        return None
                    item = data.get("item", {})
                    if item.get("type") == "agent_message":
                        return item.get("text")
                    return None

                json_content_extractor = (
                    extract_codex_content if self.config.cli == AgentCLI.CODEX else None
                )

                def create_session():
                    # Use CLI strategy's create_session method
                    return cli_strategy.create_session()

                def update_cmd_with_session(cmd_list, new_session_id):
                    if "resume" in cmd_list:
                        resume_idx = cmd_list.index("resume")
                        cmd_list[resume_idx + 1] = new_session_id
                    elif "--resume" in cmd_list:
                        resume_idx = cmd_list.index("--resume")
                        cmd_list[resume_idx + 1] = new_session_id
                    return cmd_list

                # Only use response parser for stream-json formats
                parser = (lambda lines: self._parse_using_strategy(cli_strategy, lines)) if parse_stream_json else None

                agent_response = self._execute_with_session_recovery(
                    cmd=cmd,
                    cli_name=self.config.cli.value.capitalize(),
                    create_new_session_fn=create_session,
                    update_cmd_with_session_fn=update_cmd_with_session,
                    response_parser=parser,
                    parse_stream_json=parse_stream_json,
                    json_content_extractor=json_content_extractor,
                    streaming_output_file=streaming_output_file,
                )
            else:
                # Only use response parser for stream-json formats
                parser = (lambda lines: self._parse_using_strategy(cli_strategy, lines)) if parse_stream_json else None

                def extract_codex_content(data: dict) -> Optional[str]:
                    if data.get("type") != "item.completed":
                        return None
                    item = data.get("item", {})
                    if item.get("type") == "agent_message":
                        return item.get("text")
                    return None

                json_content_extractor = (
                    extract_codex_content if self.config.cli == AgentCLI.CODEX else None
                )

                agent_response = self._execute_with_streaming(
                    cmd=cmd,
                    cli_name=self.config.cli.value.capitalize(),
                    response_parser=parser,
                    parse_stream_json=parse_stream_json,
                    json_content_extractor=json_content_extractor,
                    streaming_output_file=streaming_output_file,
                )

            # Extract session ID if needed
            if not self.config.session_id:
                session_id = cli_strategy.extract_session_id(agent_response.streaming_log or [])
                if session_id:
                    self.config.session_id = session_id

            # Add CLI command args to response
            agent_response.cli_command_args = cmd[1:]

            # Accumulate token usage
            self._total_token_usage.input_tokens += agent_response.token_usage.input_tokens
            self._total_token_usage.output_tokens += agent_response.token_usage.output_tokens
            self._total_token_usage.cache_creation_input_tokens += agent_response.token_usage.cache_creation_input_tokens
            self._total_token_usage.cache_read_input_tokens += agent_response.token_usage.cache_read_input_tokens
            self._total_token_usage.total_cost_usd += agent_response.token_usage.total_cost_usd
            if agent_response.token_usage.turn_usages:
                self._total_token_usage.turn_usages.extend(agent_response.token_usage.turn_usages)

            return agent_response
        except AgentExecutionError:
            raise
        except Exception as e:
            raise AgentExecutionError(f"Agent execution failed: {e}") from e

    def _parse_using_strategy(self, cli_strategy: AbstractCLI, output_lines: List[str]) -> AgentResponse:
        """Parse response using the CLI strategy.

        Args:
            cli_strategy: CLI strategy instance
            output_lines: Output lines from CLI

        Returns:
            AgentResponse
        """
        response, token_usage, permission_denials = cli_strategy.parse_response(output_lines)
        return AgentResponse(
            response=response,
            token_usage=token_usage,
            permission_denials=permission_denials
        )

    def get_total_token_usage(self) -> TokenUsage:
        """Get total accumulated token usage across all execute() calls.

        Returns:
            Total token usage statistics
        """
        return self._total_token_usage

    def _execute_with_session_recovery(
        self,
        cmd: List[str],
        cli_name: str,
        create_new_session_fn: Callable[[], str],
        update_cmd_with_session_fn: Callable[[List[str], str], List[str]],
        max_retries: int = 3,
        _retry_count: int = 0,
        **streaming_kwargs
    ) -> AgentResponse:
        """Generic session recovery wrapper for all CLIs with session support.

        This method wraps _execute_with_streaming to automatically handle session
        not found errors by creating a new session and retrying.

        Args:
            cmd: Command to execute
            cli_name: Name of CLI (for display)
            create_new_session_fn: Function to create a new session, returns session_id
            update_cmd_with_session_fn: Function to update cmd with new session_id
            max_retries: Maximum number of retry attempts (default: 3)
            _retry_count: Internal counter for recursive retries
            **streaming_kwargs: Arguments passed to _execute_with_streaming

        Returns:
            AgentResponse

        Raises:
            AgentExecutionError: If execution fails with non-session error or max retries exceeded
        """
        try:
            return self._execute_with_streaming(cmd=cmd, cli_name=cli_name, **streaming_kwargs)
        except AgentExecutionError as e:
            # Check if it's a session not found error or prompt too long error
            error_msg = str(e).lower()
            session_error_phrases = [
                "no conversation found",
                "session not found",
                "conversation does not exist"
            ]

            # Check for prompt too long error
            is_prompt_too_long = (
                hasattr(e, 'error_type') and e.error_type == "invalid_request" and
                "prompt is too long" in error_msg
            )

            is_session_error = any(phrase in error_msg for phrase in session_error_phrases)

            if is_session_error or is_prompt_too_long:
                # Check if we've exceeded max retries
                if _retry_count >= max_retries:
                    print(f"\n❌ Could not recover from error after {max_retries} attempts\n")
                    raise

                if is_prompt_too_long:
                    # Handle prompt too long error: create fresh session
                    old_session_id = self.config.session_id
                    print(f"\n⚠️  Prompt is too long for session {old_session_id}, creating fresh session...\n")
                else:
                    # Handle session not found error
                    old_session_id = self.config.session_id
                    print(f"\n⚠️  Session {old_session_id} not found, creating new session...\n")

                # Create new session
                new_session_id = create_new_session_fn()

                # Update command with new session
                cmd = update_cmd_with_session_fn(cmd, new_session_id)

                # Update config
                self.config.session_id = new_session_id

                # Retry recursively to support multiple recovery attempts
                return self._execute_with_session_recovery(
                    cmd=cmd,
                    cli_name=cli_name,
                    create_new_session_fn=create_new_session_fn,
                    update_cmd_with_session_fn=update_cmd_with_session_fn,
                    max_retries=max_retries,
                    _retry_count=_retry_count + 1,
                    **streaming_kwargs
                )
            else:
                # Other error, re-raise
                raise

    # CLI-specific rate limit error patterns
    RATE_LIMIT_PATTERNS = {
        "claude": [
            "limit reached",
        ],
        "gemini": [
            "exhausted your capacity",
            "code: 429",
            "quota will reset",
        ],
        "copilot": [
            "rate limit",
            "status 429",
            "quota exceeded",
            "you have no quota",
            "capierror: 402",
        ],
        "cursor-agent": [
            "rate limit",
            "status 429",
            "you've hit your usage limit",
        ],
        "codex": [
            "rate limit",
            "status 429",
            "quota exceeded",
        ],
    }

    def _is_rate_limit_error(self, error_text: str) -> bool:
        """Check if error message indicates a rate limit error.

        This method checks for CLI-specific rate limit error patterns.
        Each CLI may have different error message formats.

        Args:
            error_text: Error message text

        Returns:
            True if it's a rate limit error
        """
        error_lower = error_text.lower()

        # Check all CLI-specific patterns
        for cli_name, patterns in self.RATE_LIMIT_PATTERNS.items():
            for pattern in patterns:
                if pattern.lower() in error_lower:
                    return True

        # Generic fallback patterns
        generic_patterns = [
            "resource_exhausted",
            "ratelimitexceeded",
        ]
        for pattern in generic_patterns:
            if pattern.lower() in error_lower:
                return True

        return False

    def _is_usage_summary_only(self, stderr_text: str) -> bool:
        """Check if stderr only contains usage summary (not a real error).

        Args:
            stderr_text: stderr output text

        Returns:
            True if it only contains usage summary
        """
        if not stderr_text:
            return False
        
        # Check if stderr contains usage summary markers
        has_usage = "Usage by model:" in stderr_text or "Total usage est:" in stderr_text
        
        # Check if stderr contains actual error indicators (must check before "Execution failed:")
        # because usage summary comes after error message
        lines = stderr_text.split('\n')
        
        # Look for error lines that appear BEFORE usage summary
        for i, line in enumerate(lines):
            # Stop when we reach usage summary
            if "Total usage est:" in line:
                break
            
            # Check for error indicators in lines before usage summary
            line_lower = line.lower()
            if any(indicator in line_lower for indicator in [
                "error:",
                "failed:",
                "exception",
                "traceback",
                "missing finish_reason",
            ]):
                return False  # Found real error
        
        # Only usage summary if it has usage markers and no error found before it
        return has_usage

    def _extract_codex_permission_denials_from_stderr(
        self,
        stderr_text: str,
    ) -> List[PermissionDenial]:
        """Extract sandbox-denied Codex exec_command calls from stderr."""
        if self.config.cli != AgentCLI.CODEX or not stderr_text:
            return []

        import re

        permission_denials: List[PermissionDenial] = []
        seen_commands = set()
        pattern = re.compile(
            r"exec_command failed for `([^`]+)`:.*?Sandbox\(Denied",
            re.DOTALL,
        )

        for match in pattern.finditer(stderr_text):
            raw_command = match.group(1).strip()
            shell_match = re.fullmatch(
                r"/bin/(?:zsh|bash)\s+-lc\s+(['\"])(.*)\1",
                raw_command,
                re.DOTALL,
            )
            command = shell_match.group(2) if shell_match else raw_command
            command = command.strip()

            if not command or command in seen_commands:
                continue

            seen_commands.add(command)
            permission_denials.append(
                PermissionDenial(
                    tool_name="Bash",
                    tool_input={"command": command},
                )
            )

        return permission_denials

    def _execute_with_streaming(
        self,
        cmd: List[str],
        cli_name: str,
        response_parser: Optional[Callable[[List[str]], AgentResponse]] = None,
        parse_stream_json: bool = False,
        json_content_extractor: Optional[Callable[[dict], Optional[str]]] = None,
        streaming_output_file: Optional[str] = None,
    ) -> AgentResponse:
        """Execute command with streaming output.

        Args:
            cmd: Command to execute
            cli_name: Name of the CLI (for display)
            response_parser: Optional custom parser for output lines
            parse_stream_json: Whether to parse stream-json format
            json_content_extractor: Optional function to extract content from parsed JSON.
                                   If None and parse_stream_json=True, uses default Claude extractor.
            streaming_output_file: Optional file path to write streaming output line-by-line

        Returns:
            AgentResponse with response text, token usage, and permission denials

        Raises:
            AgentExecutionError: If execution fails
        """
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,  # Close stdin to prevent CLI from waiting for input
                text=True,
                bufsize=1,  # Line buffered
            )
        except FileNotFoundError as e:
            # CLI command not found - provide user-friendly error
            cli_name = cmd[0] if cmd else "unknown"
            err = AgentExecutionError(
                f"CLI tool '{cli_name}' not found. Please install it or configure a different agent CLI in .cafe/config.yaml"
            )
            err.error_type = "cli_not_found"
            raise err from e

        # Check stderr first for immediate errors (e.g., session locked)
        import select
        import sys
        
        stderr_check_timeout = 0.5  # 500ms to check for immediate errors
        
        if sys.platform != 'win32' and process.stderr:
            # Use select on Unix-like systems to check for immediate stderr output
            ready, _, _ = select.select([process.stderr], [], [], stderr_check_timeout)

            if process.stderr in ready:
                # Read first line of stderr if available (non-blocking)
                stderr_line = process.stderr.readline()
                # Only treat as fatal error if it's NOT a tool execution error
                # Tool errors like "Error executing tool" are recoverable and agent continues
                is_tool_error = "error executing tool" in stderr_line.lower()
                is_fatal_error = (
                    stderr_line and
                    ("already in use" in stderr_line.lower() or
                     "limit reached" in stderr_line.lower() or
                     ("error" in stderr_line.lower() and not is_tool_error))
                )

                if is_fatal_error:
                    # Likely a fatal error, read rest and terminate
                    process.kill()
                    remaining_stderr = process.stderr.read()
                    full_stderr = stderr_line + remaining_stderr

                    # Check if it's a rate limit error
                    is_rate_limit = self._is_rate_limit_error(full_stderr)
                    if is_rate_limit:
                        print(f"\n❌ {cli_name} API rate limit reached\n")
                        print(f"Error message: {full_stderr.strip()}\n")
                        print(f"{'='*80}\n")

                    # Attach actual CLI arguments to error object for history recording
                    err = AgentExecutionError(
                        f"{cli_name} execution failed: {full_stderr}"
                    )
                    # Set error_type for rate limit errors
                    if is_rate_limit:
                        err.error_type = "rate_limit"
                    # Exclude executable itself (e.g. 'gemini' / 'claude')
                    err.cli_command_args = cmd[1:]
                    raise err

        # Print header
        print(f"\n{'='*80}")
        print(f"{cli_name} Response (streaming):")
        print(f"{'='*80}")

        output_lines = []
        response_text = ""
        streaming_log: List[str] = []  # Record all streaming fragments
        token_usage = TokenUsage()
        session_id = None
        model: Optional[str] = None
        permission_denials: List[PermissionDenial] = []

        # Add idle timeout to prevent hanging when process stops outputting
        import select
        import sys
        import time

        use_idle_timeout = (sys.platform != 'win32')
        # Gemini needs longer timeout (10 min), others use 5 min
        idle_timeout = 600 if self.config.cli == AgentCLI.GEMINI else 300  # seconds - timeout if no new output
        last_output_time = time.time() if use_idle_timeout else None
        idle_timeout_triggered = False  # Track if we exited due to idle timeout

        # Open streaming output file if provided
        streaming_file_handle = None
        streaming_line_index = 0
        if streaming_output_file:
            try:
                streaming_file_handle = open(streaming_output_file, 'w', encoding='utf-8')
            except Exception as e:
                print(f"⚠️  Failed to open streaming output file: {e}")

        try:
            if process.stdout:
                while True:
                    # Check if stdout has data available (with timeout)
                    if use_idle_timeout:
                        # Unix-like systems: use select with timeout to prevent indefinite blocking
                        ready, _, _ = select.select([process.stdout], [], [], 1.0)  # 1 second timeout per check

                        if not ready:
                            # No data available, check if idle timeout exceeded
                            if time.time() - last_output_time > idle_timeout:
                                print(f"\n⚠️  No output from {cli_name} for {idle_timeout}s, assuming completion...")
                                idle_timeout_triggered = True
                                break
                            continue  # Continue waiting

                    # Read the line
                    line = process.stdout.readline()
                    if not line:
                        break

                    # Update last output time (if tracking)
                    if use_idle_timeout:
                        last_output_time = time.time()

                    # Write line to streaming output file immediately
                    if streaming_file_handle:
                        try:
                            if parse_stream_json:
                                # For stream-json: write raw JSON line
                                streaming_file_handle.write(line)
                            else:
                                # For non-stream-json: wrap in JSON object with index and timestamp
                                from datetime import datetime
                                json_obj = {
                                    "index": streaming_line_index,
                                    "timestamp": datetime.now().astimezone().isoformat(),
                                    "content": line.rstrip('\n')
                                }
                                streaming_file_handle.write(json.dumps(json_obj, ensure_ascii=False) + "\n")
                                streaming_line_index += 1
                            streaming_file_handle.flush()
                        except Exception as e:
                            print(f"⚠️  Failed to write to streaming output file: {e}")

                    if parse_stream_json:
                        # Parse stream-json format
                        try:
                            data = json.loads(line.strip())

                            # Always collect the line for response_parser (e.g., Gemini needs last line)
                            output_lines.append(line)

                            # Check for error field (e.g., "invalid_request" for prompt too long)
                            if "error" in data and data.get("error") == "invalid_request":
                                # Extract error message from response text
                                error_text = response_text or ""
                                if "message" in data and "content" in data["message"]:
                                    for content_block in data["message"]["content"]:
                                        if content_block.get("type") == "text":
                                            error_text = content_block.get("text", "")

                                # Raise error with specific type for session recovery handling
                                err = AgentExecutionError(f"{cli_name} invalid request: {error_text}")
                                err.error_type = "invalid_request"
                                err.cli_command_args = cmd[1:]
                                raise err

                            # Extract session_id (from init message for Gemini, or any message for Claude)
                            if "session_id" in data and not session_id:
                                session_id = data["session_id"]
                            elif (
                                data.get("type") == "thread.started"
                                and "thread_id" in data
                                and not session_id
                            ):
                                session_id = data["thread_id"]

                            # Extract token usage (usually in final message)
                            if "usage" in data:
                                usage_data = data["usage"]
                                token_usage = TokenUsage(
                                    input_tokens=usage_data.get("input_tokens", 0),
                                    output_tokens=usage_data.get("output_tokens", 0),
                                    cache_creation_input_tokens=usage_data.get("cache_creation_input_tokens", 0),
                                    cache_read_input_tokens=usage_data.get("cache_read_input_tokens", 0),
                                )

                            if "total_cost_usd" in data:
                                token_usage.total_cost_usd = data["total_cost_usd"]

                            # Extract duration (from result message)
                            if "duration_ms" in data:
                                token_usage.duration_ms = data["duration_ms"]
                            if "duration_api_ms" in data:
                                token_usage.duration_api_ms = data["duration_api_ms"]

                            # Extract stats (Gemini format)
                            if "stats" in data:
                                stats_data = data["stats"]
                                if "total_tokens" in stats_data:
                                    token_usage.input_tokens = stats_data.get("input_tokens", 0)
                                    token_usage.output_tokens = stats_data.get("output_tokens", 0)
                                if "duration_ms" in stats_data:
                                    token_usage.duration_ms = stats_data["duration_ms"]

                            # Extract model (from init or result message)
                            if "model" in data and data["model"]:
                                model = data["model"]

                            # Check for result message (indicates completion for Gemini/Claude)
                            # When type is "result", the CLI has completed and we should stop reading
                            if data.get("type") == "result":
                                break

                            # Extract content using custom extractor or default Claude extractor
                            # FIXME: Should implement extractors seperately for each CLI
                            if json_content_extractor:
                                content = json_content_extractor(data)
                                if content:
                                    print(content, end='\n\n', flush=True)
                                    streaming_log.append(content)
                                    response_text = content  # Only save the last fragment
                            else:
                                # Default Claude format extractor
                                # Extract content from message.content[] (new Claude format)
                                if "message" in data and "content" in data["message"]:
                                    for content_block in data["message"]["content"]:
                                        if content_block.get("type") == "text":
                                            text = content_block.get("text", "")
                                            print(text, end='\n\n', flush=True)
                                            streaming_log.append(text)
                                            response_text = text  # Only save the last fragment

                                # Old format: direct content field
                                elif "content" in data:
                                    content = data["content"]
                                    print(content, end='\n\n', flush=True)
                                    streaming_log.append(content)
                                    response_text = content  # Only save the last fragment

                            # Extract permission_denials (usually in final message)
                            if "permission_denials" in data and data["permission_denials"]:
                                for denial_data in data["permission_denials"]:
                                    permission_denials.append(
                                        PermissionDenial(
                                            tool_name=denial_data["tool_name"],
                                            tool_input=denial_data["tool_input"]
                                        )
                                    )

                        except json.JSONDecodeError:
                            # Non-JSON line, just print it
                            print(line, end='')
                            output_lines.append(line)
                    else:
                        # Simple line-by-line streaming (Copilot style)
                        print(line, end='')
                        output_lines.append(line)
                        streaming_log.append(line)  # Record each line to streaming_log
        except KeyboardInterrupt:
            print(f"\n\n⚠️  Interrupted by user, terminating {cli_name} process...")
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                print(f"⚠️  Process did not respond to SIGTERM, sending SIGKILL...")
                process.kill()
                process.wait(timeout=2)
            # Close streaming file handle if open
            if streaming_file_handle:
                streaming_file_handle.close()
            raise

        print(f"\n{'='*80}\n")

        # If idle timeout triggered, terminate process immediately
        if idle_timeout_triggered:
            print(f"⚠️  Terminating {cli_name} process due to idle timeout...")
            process.terminate()
            try:
                returncode = process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                print(f"⚠️  Process did not respond to SIGTERM, sending SIGKILL...")
                process.kill()
                try:
                    returncode = process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    # If even kill doesn't work, something is very wrong
                    print(f"❌ Process could not be killed, giving up...")
                    returncode = -1

            # Read stderr after termination
            stderr_output = process.stderr.read() if process.stderr else ""

            # Treat as success if we got output
            if output_lines:
                print(f"✓ Got output from {cli_name}, treating as success despite idle timeout")
                returncode = 0
        else:
            # Add timeout to prevent hanging (especially for copilot)
            # Timeout starts after all output has been read from stdout
            # If timeout, terminate and treat as success if we got output
            try:
                returncode = process.wait(timeout=300)
                # Only read stderr after process completes normally
                stderr_output = process.stderr.read() if process.stderr else ""
            except subprocess.TimeoutExpired:
                print(f"⚠️  {cli_name} process did not exit within timeout, terminating...")
                process.terminate()
                try:
                    returncode = process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    returncode = process.wait()

                # Read stderr after termination
                stderr_output = process.stderr.read() if process.stderr else ""

                # If we got output, treat timeout as success (agent likely finished but didn't exit)
                if output_lines:
                    print(f"✓ Got output from {cli_name}, treating as success despite timeout")
                    returncode = 0

        if returncode != 0:
            # Check if stderr only contains usage summary (Copilot may output usage to stderr)
            if stderr_output and self._is_usage_summary_only(stderr_output):
                # Treat as success if we got valid output and stderr is just usage summary
                if output_lines:
                    print(f"✓ Got valid output from {cli_name}, ignoring non-zero exit code (stderr contains only usage summary)")
                    returncode = 0
            
            # If still non-zero, it's a real error
            if returncode != 0:
                # Check if it's a rate limit error
                is_rate_limit = stderr_output and self._is_rate_limit_error(stderr_output)
                if is_rate_limit:
                    print(f"\n❌ {cli_name} API rate limit reached\n")
                    print(f"Error message: {stderr_output.strip()}\n")

                err = AgentExecutionError(
                    f"{cli_name} execution failed with code {returncode}: {stderr_output}"
                )
                # Set error_type for rate limit errors
                if is_rate_limit:
                    err.error_type = "rate_limit"
                # Attach actual CLI arguments for Phase to write to iteration history on error
                err.cli_command_args = cmd[1:]
                raise err

        # Append stderr to streaming output file (for debugging token usage parsing)
        if streaming_file_handle and stderr_output:
            try:
                from datetime import datetime
                stderr_obj = {
                    "index": streaming_line_index,
                    "timestamp": datetime.now().astimezone().isoformat(),
                    "type": "stderr",
                    "content": stderr_output.rstrip('\n'),
                }
                streaming_file_handle.write(json.dumps(stderr_obj, ensure_ascii=False) + "\n")
                streaming_file_handle.flush()
            except Exception as e:
                print(f"⚠️  Failed to write stderr to streaming output file: {e}")

        # Close streaming output file
        if streaming_file_handle:
            try:
                streaming_file_handle.close()
            except Exception as e:
                print(f"⚠️  Failed to close streaming output file: {e}")

        # Save session_id if extracted (always update to handle session expiration)
        if session_id:
            self.config.session_id = session_id

        codex_permission_denials = self._extract_codex_permission_denials_from_stderr(stderr_output)
        permission_denials.extend(codex_permission_denials)

        # Use custom response parser if provided
        if response_parser:
            parsed_response = response_parser(output_lines)
            if codex_permission_denials:
                existing_pairs = {
                    (denial.tool_name, json.dumps(denial.tool_input, sort_keys=True))
                    for denial in parsed_response.permission_denials
                }
                for denial in codex_permission_denials:
                    key = (denial.tool_name, json.dumps(denial.tool_input, sort_keys=True))
                    if key not in existing_pairs:
                        parsed_response.permission_denials.append(denial)
                        existing_pairs.add(key)
            # Merge streaming_log from custom parser with accumulated streaming_log
            # If parser doesn't provide streaming_log, use our accumulated one
            if not parsed_response.streaming_log:
                parsed_response.streaming_log = streaming_log if streaming_log else []
            # Preserve model extracted during streaming
            if model is not None:
                parsed_response.model = model
            # Preserve duration extracted during streaming (parser doesn't have this info)
            if token_usage.duration_ms is not None:
                parsed_response.token_usage.duration_ms = token_usage.duration_ms
            if token_usage.duration_api_ms is not None:
                parsed_response.token_usage.duration_api_ms = token_usage.duration_api_ms
            return parsed_response

        # Return response (either from stream-json or combined lines)
        if parse_stream_json:
            # response_text is already the last fragment, use output_lines if empty
            final_response = response_text if response_text else ''.join(output_lines)
            # streaming_log contains extracted text content for context.json
            final_streaming_log = streaming_log if streaming_log else []
        else:
            # Non-stream-json style (Copilot): parse response to extract token usage
            # Get CLI strategy instance to parse the response
            from cafe.agents.cli.copilot import CopilotCLI
            
            cli_strategy = CopilotCLI(self.config)
            # Parse response to extract token usage and clean response
            # Pass stderr_output separately as usage summary may be in stderr
            parse_result = cli_strategy.parse_response(
                output_lines, stderr_output=stderr_output
            )
            
            # Check if parser returns model (4-tuple) or not (3-tuple)
            if len(parse_result) == 4:
                final_response, token_usage, parsed_denials, parsed_model = parse_result
                # Use parsed model if available
                if parsed_model:
                    model = parsed_model
            else:
                # Old 3-tuple format (backward compatibility)
                final_response, token_usage, parsed_denials = parse_result
            
            # Merge any permission denials from parsing with those already collected
            permission_denials.extend(parsed_denials)
            final_streaming_log = output_lines

        # Model is already tracked separately, duration stays in token_usage
        return AgentResponse(
            response=final_response,
            token_usage=token_usage,
            permission_denials=permission_denials,
            streaming_log=final_streaming_log,
            model=model,
        )
