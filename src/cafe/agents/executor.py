"""Agent executor for running AI agents."""

import json
import subprocess
from pathlib import Path
from typing import Callable, List, Optional, Tuple

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
            "edit": "replace",
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
    }

    def __init__(self, config: AgentConfig) -> None:
        """Initialize agent executor.

        Args:
            config: Agent configuration
        """
        self.config = config
        self._total_token_usage = TokenUsage()

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

    def execute(self, prompt: str, allowed_tools: Optional[List[str]] = None) -> AgentResponse:
        """Execute the agent with given prompt.

        Args:
            prompt: Prompt to send to the agent
            allowed_tools: List of allowed tools (using Claude naming convention)

        Returns:
            AgentResponse with response text, token usage, and permission denials

        Raises:
            AgentExecutionError: If agent execution fails
        """
        # Translate tool names to the appropriate CLI convention
        translated_tools = self._translate_tool_names(allowed_tools)

        try:
            if self.config.cli == AgentCLI.CLAUDE:
                agent_response = self._execute_claude(prompt, translated_tools)
            elif self.config.cli == AgentCLI.GEMINI:
                agent_response = self._execute_gemini(prompt, translated_tools)
            elif self.config.cli == AgentCLI.CURSOR:
                agent_response = self._execute_cursor(prompt, translated_tools)
            elif self.config.cli == AgentCLI.COPILOT:
                agent_response = self._execute_copilot(prompt, translated_tools)
            else:
                raise AgentExecutionError(f"Unsupported agent CLI: {self.config.cli}")

            # Accumulate token usage
            self._total_token_usage.input_tokens += agent_response.token_usage.input_tokens
            self._total_token_usage.output_tokens += agent_response.token_usage.output_tokens
            self._total_token_usage.cache_creation_input_tokens += agent_response.token_usage.cache_creation_input_tokens
            self._total_token_usage.cache_read_input_tokens += agent_response.token_usage.cache_read_input_tokens
            self._total_token_usage.total_cost_usd += agent_response.token_usage.total_cost_usd

            return agent_response
        except AgentExecutionError:
            raise
        except Exception as e:
            raise AgentExecutionError(f"Agent execution failed: {e}") from e

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
            # Check if it's a session not found error
            error_msg = str(e).lower()
            session_error_phrases = [
                "no conversation found",
                "session not found",
                "conversation does not exist"
            ]

            if any(phrase in error_msg for phrase in session_error_phrases):
                # Check if we've exceeded max retries
                if _retry_count >= max_retries:
                    print(f"\n❌ Could not recover from error after {max_retries} attempts\n")
                    raise

                # Get old session ID from config
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

    def _execute_with_streaming(
        self,
        cmd: List[str],
        cli_name: str,
        response_parser: Optional[Callable[[List[str]], AgentResponse]] = None,
        parse_stream_json: bool = False,
        json_content_extractor: Optional[Callable[[dict], Optional[str]]] = None,
    ) -> AgentResponse:
        """Execute command with streaming output.

        Args:
            cmd: Command to execute
            cli_name: Name of the CLI (for display)
            response_parser: Optional custom parser for output lines
            parse_stream_json: Whether to parse stream-json format
            json_content_extractor: Optional function to extract content from parsed JSON.
                                   If None and parse_stream_json=True, uses default Claude extractor.

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
                text=True,
                bufsize=1,  # Line buffered
            )
        except FileNotFoundError as e:
            # CLI command not found - provide user-friendly error
            cli_name = cmd[0] if cmd else "unknown"
            raise AgentExecutionError(
                f"CLI tool '{cli_name}' not found. Please install it or configure a different agent CLI in .cafe/config.yaml"
            ) from e

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
                if stderr_line and ("already in use" in stderr_line.lower() or "error:" in stderr_line.lower() or "limit reached" in stderr_line.lower()):
                    # Likely a fatal error, read rest and terminate
                    process.kill()
                    remaining_stderr = process.stderr.read()
                    full_stderr = stderr_line + remaining_stderr

                    # Check if it's a rate limit error
                    if "limit reached" in full_stderr.lower():
                        print(f"\n❌ {cli_name} API 使用量已達上限\n")
                        print(f"錯誤訊息: {full_stderr.strip()}\n")
                        print(f"{'='*80}\n")

                    # Attach實際 CLI 參數到錯誤物件，方便記錄到 history
                    err = AgentExecutionError(
                        f"{cli_name} execution failed: {full_stderr}"
                    )
                    # 不包含可執行檔本身（例如 'gemini' / 'claude'）
                    err.cli_command_args = cmd[1:]
                    raise err

        # Print header
        print(f"\n{'='*80}")
        print(f"{cli_name} Response (streaming):")
        print(f"{'='*80}")

        output_lines = []
        response_text = ""
        streaming_log: List[str] = []  # 記錄所有 streaming 片段
        token_usage = TokenUsage()
        session_id = None
        permission_denials: List[PermissionDenial] = []

        if process.stdout:
            for line in iter(process.stdout.readline, ''):
                if not line:
                    break

                if parse_stream_json:
                    # Parse stream-json format
                    try:
                        data = json.loads(line.strip())

                        # Always collect the line for response_parser (e.g., Gemini needs last line)
                        output_lines.append(line)

                        # Extract content using custom extractor or default Claude extractor
                        # FIXME: Should implement extractors seperately for each CLI
                        if json_content_extractor:
                            content = json_content_extractor(data)
                            if content:
                                print(content, end='\n\n', flush=True)
                                streaming_log.append(content)
                                response_text = content  # 只保存最後一個片段
                        else:
                            # Default Claude format extractor
                            # Extract content from message.content[] (new Claude format)
                            if "message" in data and "content" in data["message"]:
                                for content_block in data["message"]["content"]:
                                    if content_block.get("type") == "text":
                                        text = content_block.get("text", "")
                                        print(text, end='\n\n', flush=True)
                                        streaming_log.append(text)
                                        response_text = text  # 只保存最後一個片段

                            # Old format: direct content field
                            elif "content" in data:
                                content = data["content"]
                                print(content, end='\n\n', flush=True)
                                streaming_log.append(content)
                                response_text = content  # 只保存最後一個片段

                        # Extract session_id
                        if "session_id" in data and not session_id:
                            session_id = data["session_id"]

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
                    streaming_log.append(line)  # 記錄每一行到 streaming_log

        print(f"\n{'='*80}\n")

        # Wait for process to complete
        stderr_output = process.stderr.read() if process.stderr else ""
        returncode = process.wait()

        if returncode != 0:
            # Check if it's a rate limit error
            if stderr_output and "limit reached" in stderr_output.lower():
                print(f"\n❌ {cli_name} API 使用量已達上限\n")
                print(f"錯誤訊息: {stderr_output.strip()}\n")

            err = AgentExecutionError(
                f"{cli_name} execution failed with code {returncode}: {stderr_output}"
            )
            # 附上實際 CLI 參數，方便 Phase 在錯誤時寫入 iteration history
            err.cli_command_args = cmd[1:]
            raise err

        # Save session_id if extracted
        if session_id and not self.config.session_id:
            self.config.session_id = session_id

        # Use custom response parser if provided
        if response_parser:
            return response_parser(output_lines)

        # Return response (either from stream-json or combined lines)
        if parse_stream_json:
            # response_text 已經是最後一個片段，如果為空則使用 output_lines
            final_response = response_text if response_text else ''.join(output_lines)
            final_streaming_log = streaming_log if streaming_log else []
        else:
            # Copilot/non-JSON style: 完整輸出作為 response
            # 所有行（包含換行符）組合成完整文本
            final_response = ''.join(output_lines) if output_lines else ""
            final_streaming_log = output_lines

        return AgentResponse(
            response=final_response,
            token_usage=token_usage,
            permission_denials=permission_denials,
            streaming_log=final_streaming_log
        )

    def _execute_claude(self, prompt: str, allowed_tools: Optional[List[str]] = None) -> AgentResponse:
        """Execute Claude agent with streaming output.

        Args:
            prompt: Prompt to send to Claude
            allowed_tools: List of allowed tools (already translated)

        Returns:
            AgentResponse with response text, token usage, and permission denials
        """
        # Step 1: Get or create session
        # If session_id already exists in config, use it; otherwise create new
        if self.config.session_id:
            session_id = self.config.session_id
        else:
            session_id = self._create_new_session()
            # Update config immediately so session recovery can access it
            self.config.session_id = session_id

        # Step 2: Use resume with actual prompt
        cmd = ["claude", "--resume", session_id, "-p", prompt]

        # Add model if specified in config
        if self.config.model:
            cmd.extend(["--model", self.config.model])

        # Add allowed tools if specified
        # Claude 的 --allowed-tools 需要雙引號，否則授權無效
        tools_arg_value = None
        if allowed_tools:
            # Special handling for Claude: convert absolute paths to git ignore format
            processed_tools = []

            for tool in allowed_tools:
                # 處理帶路徑的工具（例如 write(/abs/path) 或 edit(/abs/path)）
                if "(" in tool and ")" in tool:
                    tool_name = tool.split("(")[0].lower()
                    path_part = tool.split("(")[1].rstrip(")")

                    # Claude 需要將路徑轉換為 git ignore 格式
                    # Git ignore 格式：以 / 開頭的相對路徑（例如 /.cafe/...）
                    # 普通相對路徑：不帶 / 前綴（例如 .cafe/...）- Phase 傳來的格式
                    # 絕對路徑：系統絕對路徑（例如 /Users/me/repo/.cafe/...）
                    path_obj = Path(path_part)

                    # 區分路徑類型：
                    # 1. 如果已經是 git ignore 格式（以 /. 或 /src 等開頭），保持不變
                    # 2. 如果是絕對路徑（例如 /Users/...），轉換為 git ignore 格式
                    # 3. 如果是普通相對路徑（例如 .cafe/...），轉換為 git ignore 格式（加前綴 /）
                    is_git_ignore_format = path_part.startswith("/") and not path_obj.is_absolute()

                    if is_git_ignore_format:
                        # 已經是 git ignore 格式，直接使用
                        processed_tool = tool
                    elif path_obj.is_absolute():
                        # 系統絕對路徑，轉換為 git ignore 格式
                        try:
                            repo_root = get_repo_root()
                            git_ignore_path = to_git_ignore_path(path_obj, repo_root)
                            processed_tool = f"{tool_name.capitalize()}({git_ignore_path})"
                        except (ValueError, OSError):
                            # 轉換失敗，使用原始路徑
                            processed_tool = tool
                    else:
                        # 普通相對路徑（例如 .cafe/...），轉換為 git ignore 格式（加前綴 /）
                        git_ignore_path = "/" + path_part
                        processed_tool = f"{tool_name.capitalize()}({git_ignore_path})"
                else:
                    # 沒有路徑參數的工具
                    processed_tool = tool

                # Avoid duplicates
                if processed_tool not in processed_tools:
                    processed_tools.append(processed_tool)

            tools_arg_value = ",".join(processed_tools)
            cmd.extend(["--allowed-tools", tools_arg_value])

        # Add streaming output format
        cmd.extend(["--output-format", "stream-json", "--verbose"])

        # Include .cafe directory for tool access
        cmd.extend(["--add-dir", ".cafe"])

        # Execute with streaming - with session recovery
        def create_session():
            return self._create_new_session()

        def update_cmd(cmd, new_session_id):
            resume_idx = cmd.index("--resume")
            cmd[resume_idx + 1] = new_session_id
            return cmd

        agent_response = self._execute_with_session_recovery(
            cmd=cmd,
            cli_name="Claude",
            create_new_session_fn=create_session,
            update_cmd_with_session_fn=update_cmd,
            parse_stream_json=True,
        )

        # Session ID is already set in config (either from existing or newly created)
        # and updated by _execute_with_session_recovery if recovery was needed

        # Add CLI command args to response（實際執行的參數列表，不包含可執行檔本身）
        # 例如：["--resume", "session", "-p", "prompt", "--output-format", ...]
        agent_response.cli_command_args = cmd[1:]

        return agent_response

    def _create_new_session(self) -> str:
        """Create a new Claude session.

        Returns:
            New session ID

        Raises:
            AgentExecutionError: If session creation fails
        """
        cmd = ["claude", "-p", "Say 'hi'", "--output-format", "json"]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )

        # Check for rate limit error first (appears in stderr as plain text)
        if result.stderr and "limit reached" in result.stderr.lower():
            print(f"\n❌ Claude API 使用量已達上限\n")
            print(f"錯誤訊息: {result.stderr.strip()}\n")
            raise AgentExecutionError(f"Claude API rate limit reached: {result.stderr}")

        try:
            response_data = json.loads(result.stdout)

            # Check for errors (like limit reached)
            if response_data.get("is_error"):
                error_msg = response_data.get("result", "Unknown error")
                # Check if it's a rate limit error
                if "limit" in error_msg.lower():
                    print(f"\n❌ Claude API 使用量已達上限\n")
                    print(f"錯誤訊息: {error_msg}\n")
                else:
                    print(f"\n⚠️  Claude API Error: {error_msg}\n")
                raise AgentExecutionError(f"Claude API error: {error_msg}")

            session_id = response_data.get("session_id")
            if not session_id:
                raise AgentExecutionError("No session_id in response")
            return session_id
        except json.JSONDecodeError as e:
            # If can't parse JSON, check returncode
            if result.returncode != 0:
                print(f"\n⚠️  Failed to create Claude session")
                if result.stderr:
                    print(f"Error: {result.stderr}\n")
                raise AgentExecutionError(f"Failed to create new session: {result.stderr}")
            raise AgentExecutionError(
                f"Failed to parse session creation response: {e}"
            ) from e

    def _ensure_geminiignore(self) -> None:
        """Ensure .geminiignore file exists with proper configuration.

        Gemini CLI needs .geminiignore to exclude .cafe directory from indexing.
        """
        from pathlib import Path

        geminiignore_path = Path(".geminiignore")
        required_pattern = "!/.cafe"

        # Check if file exists and has the required pattern
        if geminiignore_path.exists():
            content = geminiignore_path.read_text()
            if required_pattern in content:
                return  # Already configured correctly
            # File exists but missing pattern - append it
            with open(geminiignore_path, "a") as f:
                if not content.endswith("\n"):
                    f.write("\n")
                f.write(f"{required_pattern}\n")
        else:
            # Create new .geminiignore file
            geminiignore_path.write_text(f"{required_pattern}\n")

    def _execute_gemini(self, prompt: str, allowed_tools: Optional[List[str]] = None) -> AgentResponse:
        """Execute Gemini agent with streaming output.

        Args:
            prompt: Prompt to send to Gemini
            allowed_tools: List of allowed tools (already translated)

        Returns:
            AgentResponse with response text, token usage, and permission denials
        """
        # Ensure .geminiignore exists with proper configuration
        self._ensure_geminiignore()

        # Build command
        cmd = ["gemini", "-p", prompt]

        # Add model if specified in config
        if self.config.model:
            cmd.extend(["--model", self.config.model])

        # Add allowed tools if specified
        tools_arg_value = None
        if allowed_tools:
            # Special handling for Gemini: write_file doesn't support path restrictions
            # write_file(/path) is treated as "no write permission", so we strip the path
            processed_tools = []
            for tool in allowed_tools:
                if tool.startswith("write_file("):
                    # Strip path parameter: write_file(/path) -> write_file
                    tool_name = "write_file"
                else:
                    tool_name = tool

                # Avoid duplicates
                if tool_name not in processed_tools:
                    processed_tools.append(tool_name)

            tools_arg_value = ",".join(processed_tools)
            cmd.extend(["--allowed-tools", tools_arg_value])

        # Add streaming JSON output format
        cmd.extend(["--output-format", "stream-json"])

        # Include .cafe directory for tool access
        cmd.extend(["--include-directories", ".cafe"])

        # Gemini-specific parser: parse last line as final result
        def parse_gemini_response(output_lines: List[str]) -> AgentResponse:
            full_output = ''.join(output_lines)

            # Parse the last line as JSON (stream-json format sends final result on last line)
            try:
                lines = [l.strip() for l in output_lines if l.strip()]
                if not lines:
                    return AgentResponse(response="", token_usage=TokenUsage())

                last_json = json.loads(lines[-1])

                # Extract only assistant messages from the full response
                # The response field contains the entire conversation, but we only want the assistant's messages
                assistant_messages = []
                permission_denials: List[PermissionDenial] = []

                for line in lines:
                    try:
                        data = json.loads(line)
                        if data.get("type") == "message" and data.get("role") == "assistant":
                            content = data.get("content", "")
                            if content:
                                assistant_messages.append(content)

                        # Extract permission_denials from stats.tools.byName[tool].decisions.reject
                        # Gemini uses different format - extract from stats if available
                        if "stats" in data and "tools" in data["stats"]:
                            tools_stats = data["stats"]["tools"]
                            if "byName" in tools_stats:
                                for tool_name, tool_stats in tools_stats["byName"].items():
                                    decisions = tool_stats.get("decisions", {})
                                    # If there are rejected decisions, we need more info
                                    # For now, skip Gemini permission denials (will implement later)
                                    pass

                    except (json.JSONDecodeError, KeyError):
                        continue

                # If we extracted assistant messages, use those; otherwise fall back to full response
                response = "".join(assistant_messages) if assistant_messages else last_json.get("response", full_output)

                # Parse token usage if available
                token_usage = TokenUsage()

                return AgentResponse(
                    response=response,
                    token_usage=token_usage,
                    permission_denials=permission_denials
                )
            except json.JSONDecodeError:
                # If not JSON, return raw output
                return AgentResponse(response=full_output, token_usage=TokenUsage())

        # Gemini content extractor function
        def extract_gemini_content(data: dict) -> Optional[str]:
            """Extract content from Gemini stream-json format.
            
            Only extract content from assistant messages, not user messages (prompt echo).
            """
            # Only extract content if it's from the assistant
            if data.get("role") == "assistant":
                return data.get("content")
            return None

        # Execute Gemini
        agent_response = self._execute_with_streaming(
            cmd,
            "Gemini",
            parse_gemini_response,
            parse_stream_json=True,
            json_content_extractor=extract_gemini_content
        )

        # Add CLI command args to response
        # 使用實際執行的參數（不包含程式名稱）
        agent_response.cli_command_args = cmd[1:]

        return agent_response

    def _execute_cursor(self, prompt: str, allowed_tools: Optional[List[str]] = None) -> AgentResponse:
        """Execute Cursor agent with streaming output.

        Args:
            prompt: Prompt to send to Cursor
            allowed_tools: List of allowed tools (ignored - cursor-agent doesn't support permission control)

        Returns:
            AgentResponse with response text, token usage, and permission denials
        """
        # Build command
        cmd = ["cursor-agent", "-p", prompt]

        # Add model if specified in config
        if self.config.model:
            cmd.extend(["--model", self.config.model])

        # Add session if configured
        if self.config.session_id:
            cmd.extend(["--resume", self.config.session_id])

        # Cursor-agent doesn't support allowed-tools, use --force to auto-approve all tools
        cmd.append("--force")

        # Add JSON output format for parsing
        cmd.extend(["--output-format", "json"])

        # Cursor-specific parser: parse JSON output
        def parse_cursor_response(output_lines: List[str]) -> AgentResponse:
            full_output = ''.join(output_lines)

            # Parse JSON output
            try:
                data = json.loads(full_output.strip())
                response = data.get("response", full_output)
                token_usage = TokenUsage()
                # TODO: Parse permission_denials from cursor if available
                return AgentResponse(response=response, token_usage=token_usage)
            except json.JSONDecodeError:
                # If not JSON, return raw output
                return AgentResponse(response=full_output, token_usage=TokenUsage())

        # Execute with session recovery if session_id is configured
        if self.config.session_id:
            def create_session():
                # Cursor creates session automatically, return empty string
                return ""

            def update_cmd(cmd, new_session_id):
                # Update --resume argument
                if "--resume" in cmd:
                    resume_idx = cmd.index("--resume")
                    cmd[resume_idx + 1] = new_session_id
                return cmd

            agent_response = self._execute_with_session_recovery(
                cmd=cmd,
                cli_name="Cursor",
                create_new_session_fn=create_session,
                update_cmd_with_session_fn=update_cmd,
                response_parser=parse_cursor_response,
            )
        else:
            agent_response = self._execute_with_streaming(cmd, "Cursor", parse_cursor_response)

        # Add CLI command args to response
        agent_response.cli_command_args = cmd[1:]

        return agent_response

    def _execute_copilot(self, prompt: str, allowed_tools: Optional[List[str]] = None) -> AgentResponse:
        """Execute GitHub Copilot CLI agent with streaming output.

        Args:
            prompt: Prompt to send to Copilot
            allowed_tools: List of allowed tools (already translated)

        Returns:
            AgentResponse with response text, token usage, and permission denials
        """
        from pathlib import Path
        import time

        # Copilot 的 session 目錄
        copilot_session_dir = Path.home() / ".copilot" / "session-state"

        # 記錄執行前的 session 檔案（用於偵測新建立的 session）
        existing_sessions = set()
        if copilot_session_dir.exists():
            existing_sessions = {f.name for f in copilot_session_dir.iterdir() if f.is_file()}

        # Build command: copilot -p "prompt" --allow-all-tools or --allow-tool
        cmd = ["copilot", "-p", prompt]

        # Add model if specified in config
        if self.config.model:
            cmd.extend(["--model", self.config.model])

        # Add allowed tools if specified
        if allowed_tools:
            # Use --allow-tool for each tool
            for tool in allowed_tools:
                cmd.extend(["--allow-tool", tool])
        else:
            # Use --allow-all-tools for automatic approval
            cmd.append("--allow-all-tools")

        # Add session if configured
        if self.config.session_id:
            cmd.extend(["--resume", self.config.session_id])

        # Include .cafe directory for tool access
        cmd.extend(["--add-dir", ".cafe"])

        # Execute with streaming (line-by-line mode)
        # Use session recovery if session_id is configured
        if self.config.session_id:
            def create_session():
                # Copilot creates session automatically, detect from filesystem
                # For now, just return empty string and let filesystem detection handle it
                return ""

            def update_cmd(cmd, new_session_id):
                # Update --resume argument
                if "--resume" in cmd:
                    resume_idx = cmd.index("--resume")
                    cmd[resume_idx + 1] = new_session_id
                return cmd

            agent_response = self._execute_with_session_recovery(
                cmd=cmd,
                cli_name="Copilot",
                create_new_session_fn=create_session,
                update_cmd_with_session_fn=update_cmd,
                parse_stream_json=False,
            )
        else:
            agent_response = self._execute_with_streaming(
                cmd=cmd,
                cli_name="Copilot",
                parse_stream_json=False,
            )

        # 如果還沒有 session_id，嘗試從新建立的 session 檔案中提取
        if not self.config.session_id and copilot_session_dir.exists():
            # 等待一下讓檔案系統更新
            time.sleep(0.1)
            current_sessions = {f.name for f in copilot_session_dir.iterdir() if f.is_file()}
            new_sessions = current_sessions - existing_sessions

            if new_sessions:
                # 找到新建立的 session，提取 UUID（檔名去掉 .jsonl）
                newest_session = sorted(new_sessions)[-1]  # 取最新的
                session_id = newest_session.replace(".jsonl", "")
                self.config.session_id = session_id

        # Add CLI command args to response（實際執行參數，不包含程式名稱）
        agent_response.cli_command_args = cmd[1:]

        return agent_response
