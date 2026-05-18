"""Sandbox-related Phase mixin – permission denials, allowed access, host execution."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from cafe.core.types import PermissionDenial


class PhaseSandboxMixin:
    """Shared helpers for phases that run agents inside restricted execution environments."""

    def _extract_sandbox_permission_denials_from_streaming_file(
        self,
        iteration: int,
    ) -> List["PermissionDenial"]:
        """Recover sandbox permission denials from a previous iteration's streaming.jsonl."""
        from cafe.core.types import PermissionDenial

        iteration_dir = self._get_iteration_dir(iteration)
        streaming_file = iteration_dir / "streaming.jsonl"
        if not streaming_file.exists():
            return []

        permission_denials: List[PermissionDenial] = []
        seen_commands = set()
        pattern = re.compile(
            r"exec_command failed for `([^`]+)`:.*?Sandbox\(Denied",
            re.DOTALL,
        )

        with open(streaming_file, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if data.get("type") != "stderr":
                    continue

                stderr_text = data.get("content", "")
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

    def _merge_allowed_tools(
        self,
        base_allowed_tools: List[str],
        approved_tools_from_denials: Optional[List[str]] = None,
    ) -> List[str]:
        """Merge base allowed tools and previous round's allowed tools (common method).

        This method will:
        1. Read allowed_tools from previous iteration
        2. Filter out file-specific tools from previous iteration (e.g., edit(file_path), write(file_path))
        3. Merge base_allowed_tools + filtered previous tools + newly approved tools
        4. Remove duplicates

        Args:
            base_allowed_tools: Base tool list for this phase
            approved_tools_from_denials: Newly approved tools from this round's permission denials (default empty list)

        Returns:
            Merged and deduplicated tool list
        """
        approved_tools_from_denials = approved_tools_from_denials or []

        # Load previous iteration's allowed tools
        prev_allowed_tools: List[str] = []
        prev_data = self._load_previous_iteration_data()
        if prev_data and prev_data.get("allowed_tools"):
            prev_allowed_tools = prev_data.get("allowed_tools", [])

        # Filter out file-specific tools from previous iteration
        # File-specific tools have format: tool_name(file_path)
        # We want to keep only generic tools (no parentheses) from previous iteration
        filtered_prev_tools = [
            tool for tool in prev_allowed_tools
            if "(" not in tool  # Filter out tools with parentheses (file-specific)
        ]

        # Merge: base tools + filtered previous iteration's tools + newly approved tools from denials
        # Use set to avoid duplicates, then convert back to list
        return list(set(base_allowed_tools + filtered_prev_tools + approved_tools_from_denials))

    def _get_allowed_directories(self) -> List[str]:
        """Get list of allowed directories.

        Return list of directories that need to be accessed by underlying CLI tools.
        Default returns [".cafe"], allowing all CLI tools to read .cafe directory.

        Returns:
            List of allowed directories
        """
        return [".cafe"]

    def _handle_previous_permission_denials(self) -> tuple[List[str], str]:
        """Handle previous round's permission denials, return approved tools and user input.

        This method handles two modes:
        1. Interactive mode: Ask user one by one whether to approve each denied tool
        2. Non-interactive mode: Use self.approved_denial_indices to approve tools

        Returns:
            tuple[List[str], str]: (List of approved tools, user's additional notes about permissions)
        """
        from cafe.core.types import PermissionDenial

        # Load previous round iteration data
        prev_data = self._load_previous_iteration_data()
        if not prev_data:
            return ([], "")

        # Only handle permission denials if previous status was need_permission
        prev_status = self._context_status_code(prev_data) or ""
        if prev_status != "need_permission":
            return ([], "")

        # Check if there are permission_denials
        permission_denials_data = prev_data.get("permission_denials", [])
        if not permission_denials_data and self.iteration > 1:
            recovered_denials = self._extract_sandbox_permission_denials_from_streaming_file(self.iteration - 1)
            permission_denials_data = [denial.model_dump() for denial in recovered_denials]
        if not permission_denials_data:
            return ([], "")

        # Convert dict to PermissionDenial objects
        permission_denials = [
            PermissionDenial(**denial_data) for denial_data in permission_denials_data
        ]
        prev_cli = str(prev_data.get("cli") or "").lower()
        auto_execute_indices = [
            idx
            for idx, denial in enumerate(permission_denials)
            if self._uses_host_execution_for_sandbox_denials(prev_cli)
            and self._is_auto_host_executable_sandbox_denial(denial)
        ]
        manual_indices = [
            idx
            for idx in range(len(permission_denials))
            if idx not in auto_execute_indices
        ]

        # Collect approved indices
        approved_indices: List[int] = list(auto_execute_indices)
        host_execution_note = ""
        is_host_execution_flow = self._uses_host_execution_for_sandbox_denials(prev_cli)
        request_label = "execution" if is_host_execution_flow else "permission"
        request_label_title = request_label.capitalize()
        approve_label = "Execute" if is_host_execution_flow else "Approve"
        deny_label = "Skip" if is_host_execution_flow else "Deny"

        if self.interactive:
            from cafe.ui.inquirer_prompts import prompt_list
            from rich.console import Console

            console = Console()

            if auto_execute_indices:
                console.print("[bold]Automatically executing blocked git commands on the host[/bold]")
                for idx in auto_execute_indices:
                    denial = permission_denials[idx]
                    command = denial.tool_input.get("command", "")
                    console.print(f"[{idx}] {denial.tool_name}({command})")
                console.print()

            if not manual_indices:
                host_execution_note = self._execute_approved_sandbox_commands_on_host(
                    permission_denials,
                    auto_execute_indices,
                )
                return ([], host_execution_note)

            while True:
                approved_indices = list(auto_execute_indices)

                # Interactive mode: First display all permission requests, then ask one by one
                print(f"\nThe following {request_label} requests were blocked in previous execution:\n")
                for idx in manual_indices:
                    denial = permission_denials[idx]
                    # Display tool name and main parameters
                    if "file_path" in denial.tool_input:
                        print(f"[{idx}] {denial.tool_name}({denial.tool_input['file_path']})")
                    elif "command" in denial.tool_input:
                        print(f"[{idx}] {denial.tool_name}({denial.tool_input['command']})")
                    else:
                        print(f"[{idx}] {denial.tool_name}(...)")

                print()  # Empty line separator

                # Ask for each permission one by one
                for idx in manual_indices:
                    denial = permission_denials[idx]
                    if "file_path" in denial.tool_input:
                        tool_desc = f"{denial.tool_name}({denial.tool_input['file_path']})"
                    elif "command" in denial.tool_input:
                        tool_desc = f"{denial.tool_name}({denial.tool_input['command']})"
                    else:
                        tool_desc = f"{denial.tool_name}"

                    console.print(f"[bold]{request_label_title} [{idx}][/bold]")
                    console.print(tool_desc)
                    console.print()

                    response = prompt_list(
                        message=f"{request_label_title} [{idx}]",
                        choices=[
                            {"name": approve_label, "value": "approve"},
                            {"name": deny_label, "value": "deny"},
                        ],
                    )
                    if response == "approve":
                        approved_indices.append(idx)

                denied_indices = [
                    idx for idx in manual_indices
                    if idx not in approved_indices
                ]

                console.print(f"[bold]{request_label_title} summary[/bold]")
                console.print()
                console.print(f"[bold]{approve_label}:[/bold]")
                if approved_indices:
                    for idx in approved_indices:
                        denial = permission_denials[idx]
                        if "file_path" in denial.tool_input:
                            tool_desc = f"{denial.tool_name}({denial.tool_input['file_path']})"
                        elif "command" in denial.tool_input:
                            tool_desc = f"{denial.tool_name}({denial.tool_input['command']})"
                        else:
                            tool_desc = f"{denial.tool_name}"
                        console.print(f"[{idx}] {tool_desc}")
                else:
                    console.print("(none)")

                console.print()
                console.print(f"[bold]{deny_label}:[/bold]")
                if denied_indices:
                    for idx in denied_indices:
                        denial = permission_denials[idx]
                        if "file_path" in denial.tool_input:
                            tool_desc = f"{denial.tool_name}({denial.tool_input['file_path']})"
                        elif "command" in denial.tool_input:
                            tool_desc = f"{denial.tool_name}({denial.tool_input['command']})"
                        else:
                            tool_desc = f"{denial.tool_name}"
                        console.print(f"[{idx}] {tool_desc}")
                else:
                    console.print("(none)")

                console.print()

                confirmation = prompt_list(
                    message=f"Confirm these {request_label} decisions",
                    choices=[
                        {"name": "Confirm", "value": "confirm"},
                        {"name": "Review again", "value": "review_again"},
                        {"name": "Cancel", "value": "cancel"},
                    ],
                    default="review_again",
                )

                if confirmation == "confirm":
                    break
                if confirmation == "cancel":
                    return ([], "")

            if is_host_execution_flow and approved_indices:
                host_execution_note = self._execute_approved_sandbox_commands_on_host(
                    permission_denials,
                    approved_indices,
                )

                # Ask for follow-up notes only when something was denied
            if denied_indices:
                print()
                from cafe.ui.inquirer_prompts import prompt_multiline
                user_input = prompt_multiline(
                    "Notes",
                    preamble=(
                        "If you skipped anything, say what the agent should do instead. Leave blank if none."
                        if is_host_execution_flow
                        else "If you denied anything, say what the agent should do instead. Leave blank if none."
                    ),
                )
            else:
                user_input = ""
        else:
            # Non-interactive mode: Use default approved_denial_indices
            if not hasattr(self, "approved_denial_indices"):
                raise AttributeError("Phase must have 'approved_denial_indices' attribute in non-interactive mode")
            if not hasattr(self, "user_input"):
                raise AttributeError("Phase must have 'user_input' attribute in non-interactive mode")

            approved_indices = self.approved_denial_indices
            user_input = self.user_input
            if is_host_execution_flow and approved_indices:
                host_execution_note = self._execute_approved_sandbox_commands_on_host(
                    permission_denials,
                    approved_indices,
                )

        if host_execution_note:
            if user_input:
                user_input = f"{host_execution_note}\n\n{user_input}"
            else:
                user_input = host_execution_note

        # Convert approved indices to allowed_tools format
        approved_tools: List[str] = []
        if is_host_execution_flow:
            return (approved_tools, user_input)

        for idx in approved_indices:
            if idx < len(permission_denials):
                denial = permission_denials[idx]
                approved_tools.append(denial.to_allowed_tool_pattern())

        return (approved_tools, user_input)

    def _execute_approved_sandbox_commands_on_host(
        self,
        permission_denials: List["PermissionDenial"],
        approved_indices: List[int],
    ) -> str:
        """Execute approved sandbox-blocked Bash commands on the host and return agent guidance."""
        from cafe.utils.git_utils import to_cwd_relative_path

        executed_records: List[dict] = []

        for idx in approved_indices:
            if idx >= len(permission_denials):
                continue

            denial = permission_denials[idx]
            if denial.tool_name != "Bash":
                continue

            command = denial.tool_input.get("command")
            if not isinstance(command, str) or not command.strip():
                continue

            result = subprocess.run(
                ["/bin/zsh", "-lc", command],
                check=False,
                text=True,
                capture_output=True,
            )
            executed_records.append(
                {
                    "tool_name": denial.tool_name,
                    "command": command,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "returncode": result.returncode,
                    "ok": result.returncode == 0,
                    "timestamp": datetime.now().astimezone().isoformat(),
                }
            )

        if not executed_records:
            return ""

        iteration_dir = self._get_iteration_dir(self.iteration)
        iteration_dir.mkdir(parents=True, exist_ok=True)
        host_execution_file = iteration_dir / "host_execution.json"
        with open(host_execution_file, "w", encoding="utf-8") as f:
            json.dump(executed_records, f, ensure_ascii=False, indent=2)

        try:
            display_path = to_cwd_relative_path(host_execution_file)
        except (ValueError, OSError):
            display_path = str(host_execution_file.resolve())

        success_count = sum(1 for record in executed_records if record.get("ok"))
        failure_count = len(executed_records) - success_count
        lines = [
            "Previously blocked approved commands were attempted by the host environment.",
            f"Review the execution log at {display_path}.",
        ]
        if failure_count:
            lines.append(
                f"{failure_count} approved command(s) failed. Inspect the log and decide the next step from the current session state."
            )
        if success_count:
            lines.append(
                f"{success_count} approved command(s) succeeded. Do not rerun those commands unless you have a new reason."
            )
        if not success_count:
            lines.append(
                "Do not assume the approved commands succeeded; use the log output to continue."
            )
        else:
            lines.append(
                "Use the log output to understand the current repository state before taking more action."
            )
        return "\n".join(lines)

    def _is_auto_host_executable_sandbox_denial(self, denial: "PermissionDenial") -> bool:
        """Return True when a denied command is a safe git command we can run on host."""
        if denial.tool_name != "Bash":
            return False

        command = denial.tool_input.get("command")
        if not isinstance(command, str) or not command.strip():
            return False

        allowed_git_subcommands = {"add", "commit", "status", "diff", "show", "log"}
        segments = self._split_command_for_sandbox_rules(command)
        if not segments:
            return False

        for segment in segments:
            git_subcommand = self._extract_git_subcommand(segment)
            if git_subcommand not in allowed_git_subcommands:
                return False

        return True

    def _uses_host_execution_for_sandbox_denials(self, cli: str) -> bool:
        """Return True when denied Bash commands should be resolved by host execution."""
        return cli == "codex"

    def _extract_git_subcommand(self, segment: List[str]) -> Optional[str]:
        """Extract the git subcommand from a parsed argv segment."""
        if not segment or segment[0] != "git":
            return None

        index = 1
        while index < len(segment):
            token = segment[index]
            if token in {"-C", "-c", "--git-dir", "--work-tree"}:
                index += 2
                continue
            if token.startswith("-"):
                index += 1
                continue
            return token

        return None

    def _split_command_for_sandbox_rules(self, command: str) -> List[List[str]]:
        """Split a safe shell command into argv segments for approval rules."""
        unsupported_substrings = ["$(", "`", ">", "<"]
        if any(token in command for token in unsupported_substrings):
            return []

        lexer = shlex.shlex(command, posix=True, punctuation_chars="|&;")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)

        segments: List[List[str]] = []
        current: List[str] = []
        separators = {"&&", "||", "|", ";"}

        for token in tokens:
            if token in separators:
                if current:
                    segments.append(current)
                    current = []
                continue

            if "$" in token or "*" in token or "?" in token:
                return []

            if "=" in token and current == []:
                return []

            current.append(token)

        if current:
            segments.append(current)

        filtered_segments = [segment for segment in segments if segment]
        if (
            filtered_segments and
            len(filtered_segments[0]) == 2 and
            filtered_segments[0][0] == "cd"
        ):
            filtered_segments = filtered_segments[1:]

        return filtered_segments

    def _get_sandbox_rules_file(self) -> Path:
        """Return the repo-local rules file used for temporary sandbox approvals."""
        from cafe.utils.git_utils import get_git_toplevel

        git_toplevel = get_git_toplevel()
        return git_toplevel / "codex" / "rules" / "cafe-approved.rules"

    def _ensure_sandbox_rules_excluded(self, rules_file: Path) -> None:
        """Ensure the temporary rules file is ignored by local Git metadata."""
        from cafe.utils.git_utils import get_git_dir, get_git_toplevel

        repo_root = get_git_toplevel()
        git_dir = get_git_dir()
        exclude_file = git_dir / "info" / "exclude"
        exclude_file.parent.mkdir(parents=True, exist_ok=True)

        ignore_path = "/" + str(rules_file.relative_to(repo_root))
        existing_lines: List[str] = []
        if exclude_file.exists():
            existing_lines = exclude_file.read_text(encoding="utf-8").splitlines()
            if ignore_path in existing_lines:
                return

        content = exclude_file.read_text(encoding="utf-8") if exclude_file.exists() else ""
        if content and not content.endswith("\n"):
            content += "\n"
        content += f"{ignore_path}\n"
        exclude_file.write_text(content, encoding="utf-8")

    def _persist_sandbox_approved_rules(
        self,
        permission_denials: List["PermissionDenial"],
        approved_indices: List[int],
    ) -> None:
        """Persist repo-local sandbox approval rules for approved Bash commands."""
        rules_file = self._get_sandbox_rules_file()
        rules_file.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_sandbox_rules_excluded(rules_file)

        blocks: List[str] = []
        for idx in approved_indices:
            if idx >= len(permission_denials):
                continue

            denial = permission_denials[idx]
            if denial.tool_name != "Bash":
                continue

            command = denial.tool_input.get("command")
            if not isinstance(command, str) or not command.strip():
                continue

            for segment in self._split_command_for_sandbox_rules(command):
                rule_segment = segment
                if len(segment) >= 2 and segment[0] == "git":
                    if segment[1] == "add":
                        rule_segment = ["git", "add"]
                    elif segment[1] == "commit":
                        rule_segment = ["git", "commit", "-m"] if "-m" in segment else ["git", "commit"]

                pattern = json.dumps(rule_segment, ensure_ascii=False)
                blocks.append(
                    "\n".join(
                        [
                            "prefix_rule(",
                            f"  pattern = {pattern},",
                            '  decision = "allow",',
                            '  justification = "Temporary approval from cafe permission flow.",',
                            ")",
                        ]
                    )
                )

        if not blocks:
            return

        existing = rules_file.read_text(encoding="utf-8") if rules_file.exists() else ""
        new_blocks = [block for block in blocks if block not in existing]
        if not new_blocks:
            return

        parts: List[str] = []
        if existing.strip():
            parts.append(existing.rstrip())
        else:
            parts.append("# Managed by cafe. Temporary sandbox approval rules.")
        parts.extend(new_blocks)
        rules_file.write_text("\n\n".join(parts) + "\n", encoding="utf-8")

    def _cleanup_sandbox_approval_artifacts(self) -> None:
        """Remove temporary repo-local sandbox approval artifacts."""
        try:
            rules_file = self._get_sandbox_rules_file()
        except ValueError:
            return

        if not rules_file.exists():
            return

        rules_file.unlink()
        rules_dir = rules_file.parent
        sandbox_rules_root = rules_dir.parent
        if rules_dir.exists() and not any(rules_dir.iterdir()):
            rules_dir.rmdir()
        if sandbox_rules_root.exists() and not any(sandbox_rules_root.iterdir()):
            sandbox_rules_root.rmdir()
