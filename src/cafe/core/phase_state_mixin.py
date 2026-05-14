"""Shared Phase helpers for status analysis and issue state management."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import yaml

from cafe.core.status_codes import PhaseStatusCode

if TYPE_CHECKING:
    from cafe.core.git import GitOperations


class PhaseStateMixin:
    """Mixin with status-analysis and issue-state helpers for Phase."""

    def _analyze_missing_status_code(
        self,
        agent_name: str,
        valid_intents: List[PhaseStatusCode],
    ) -> Optional[PhaseStatusCode]:
        """When response has no status code, call agent to analyze status."""
        prompt = self._get_status_analysis_prompt()
        if not prompt:
            return None

        response, _, _, _, _, _ = self.agent_manager.execute(
            agent_name, prompt, allowed_directories=self._get_allowed_directories()
        )
        return self._extract_status_code_from_response(response, valid_codes=valid_intents)

    @staticmethod
    def _infer_human_input_status_from_response(response: str) -> Optional[PhaseStatusCode]:
        """Infer human-input status codes from plain-text agent replies."""
        normalized = (response or "").lower()
        if not normalized.strip():
            return None

        permission_markers = (
            "請允許",
            "授權",
            "權限提示",
            "allow",
            "permission",
            "adjust permission",
            "調整權限",
            "需要您授權",
            "需要你授權",
            "claude code",
        )
        if any(marker in normalized for marker in permission_markers):
            return PhaseStatusCode.NEED_PERMISSION

        return None

    def _analyze_missing_status_code_with_logging(
        self,
        agent_name: str,
        valid_intents: List[PhaseStatusCode],
        original_response: str,
    ) -> tuple[Optional[str], Optional[PhaseStatusCode]]:
        """When response has no status code, call agent to analyze status (with logging)."""
        prompt = self._get_status_analysis_prompt()
        if not prompt:
            return None, None

        try:
            allowed_tools = ["read", "grep", "glob", "ls"]
            response, _, _, _, _, _ = self.agent_manager.execute(
                agent_name,
                prompt,
                allowed_tools=allowed_tools,
                allowed_directories=self._get_allowed_directories(),
            )
            status_code = self._extract_status_code_from_response(
                response,
                valid_codes=valid_intents,
            )

            return response, status_code
        except Exception as e:
            print(f"⚠️  Status code analysis failed: {e}")
            return None, None

    def _write_status_code_error_log(
        self,
        original_response: str,
        valid_intents: List[PhaseStatusCode],
        status_code: Optional[PhaseStatusCode],
        analysis_attempted: bool,
        analysis_response: Optional[str],
        cli_command_args: Optional[List[str]],
        multiple_codes_found: Optional[List[PhaseStatusCode]] = None,
    ) -> None:
        """Write status code error log to iteration error.json file."""
        if not hasattr(self, "phase_dir") or not hasattr(self, "iteration"):
            return

        if status_code is not None and not analysis_attempted and not multiple_codes_found:
            return

        iteration_dir = self._get_iteration_dir(self.iteration)
        iteration_dir.mkdir(parents=True, exist_ok=True)
        error_file = iteration_dir / "error.json"

        if multiple_codes_found:
            error_type = "Multiple status codes"
        elif status_code is None:
            error_type = "Missing status code"
        else:
            error_type = "Status code required analysis"

        error_data = {
            "error": error_type,
            "error_type": error_type.replace(" ", "_").upper(),
            "is_critical": False,
            "timestamp": datetime.now().astimezone().isoformat(),
            "issue": self.issue_dir.name if hasattr(self, "issue_dir") else "unknown",
            "iteration": self.iteration,
            "phase": self.phase_name if hasattr(self, "phase_name") else "unknown",
            "original_response": original_response,
            "valid_intents": [code.value for code in valid_intents],
            "extracted_status_code": status_code.value if status_code else None,
            "multiple_codes_found": [code.value for code in multiple_codes_found] if multiple_codes_found else None,
            "analysis_attempted": analysis_attempted,
            "analysis_response": analysis_response if analysis_attempted else None,
            "cli_command_args": cli_command_args,
        }

        with open(error_file, "w", encoding="utf-8") as f:
            json.dump(error_data, f, ensure_ascii=False, indent=2)

    def _get_status_analysis_prompt(self) -> Optional[str]:
        """Get prompt for analyzing status code (subclass override)."""
        return None

    def _read_issue_config(self, config_path: Path) -> Optional[Dict[str, Any]]:
        """Read issue configuration from config.yaml."""
        if not config_path.exists():
            return None

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config_data = yaml.safe_load(f)
            return config_data if config_data else None
        except (yaml.YAMLError, IOError):
            return None

    def _write_issue_config(self, config_path: Path, config_data: Dict[str, Any]) -> None:
        """Write issue configuration to config.yaml."""
        config_path.parent.mkdir(parents=True, exist_ok=True)

        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config_data, f, allow_unicode=True, default_flow_style=False)

    def _get_issue_config_value(self, config_file: Path, key: str) -> Optional[Any]:
        """Read a value from issue config file."""
        config_data = self._read_issue_config(config_file)
        if not config_data:
            return None

        if "." in key:
            keys = key.split(".")
            value = config_data
            for k in keys:
                if isinstance(value, dict):
                    value = value.get(k)
                    if value is None:
                        return None
                else:
                    return None
            return value
        return config_data.get(key)

    def _get_issue_dir(self, git_ops: "GitOperations") -> Path:
        """Get issue directory path based on current Git branch."""
        current_branch = git_ops.get_current_branch()
        return Path(f".cafe/issues/{current_branch}")

    def _get_current_branch_commits(self, git_ops: "GitOperations", base_branch: str) -> str:
        """Get commits from current branch that are not in base branch."""
        return git_ops.get_commits_between(base=base_branch, head="HEAD")

    def _get_versioned_file_path(
        self,
        phase_name: str,
        iteration: int,
        phase_dir: Path,
    ) -> Path:
        """Get path of versioned file."""
        iteration_dir = phase_dir / f"iteration_{iteration:03d}"
        return iteration_dir / "output.md"

    def _get_next_iteration_number(
        self,
        phase_name: str,
        phase_dir: Path,
    ) -> int:
        """Get next iteration number."""
        if not phase_dir.exists():
            return 1

        existing_iterations = sorted(phase_dir.glob("iteration_*/context.json"))
        if not existing_iterations:
            return 1

        count = len(existing_iterations)

        if count >= 999:
            raise ValueError("Cannot exceed 999")

        last_context_file = existing_iterations[-1]
        try:
            with open(last_context_file, "r", encoding="utf-8") as f:
                last_iteration_data = json.load(f)

            if not last_iteration_data.get("end_time"):
                return count
        except (json.JSONDecodeError, KeyError, FileNotFoundError):
            return count

        return count + 1

    def _copy_previous_version(
        self,
        phase_name: str,
        iteration: int,
        phase_dir: Path,
    ) -> None:
        """Copy previous version file to new version."""
        if iteration == 1:
            return

        current_file = self._get_versioned_file_path(phase_name, iteration, phase_dir)

        source_file = None
        for i in range(iteration - 1, 0, -1):
            candidate = self._get_versioned_file_path(phase_name, i, phase_dir)
            if candidate.exists():
                source_file = candidate
                break

        if source_file:
            import shutil

            current_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, current_file)

    def _get_latest_versioned_file(
        self,
        phase_name: str,
        phase_dir: Path,
    ) -> Optional[Path]:
        """Get path of latest version file."""
        iteration_dirs = sorted(phase_dir.glob("iteration_*"))

        for iteration_dir in reversed(iteration_dirs):
            output_file = iteration_dir / "output.md"
            if output_file.exists():
                return output_file

        return None

    def _check_output_file_updated(
        self,
        output_file: Path,
        iteration: int,
        phase_dir: Path,
        compare_content: Optional[str] = None,
    ) -> bool:
        """Check if output file was updated."""
        if not output_file.exists():
            return False

        current_content = output_file.read_text(encoding="utf-8").strip()

        if compare_content is not None:
            return current_content != compare_content.strip()

        if iteration == 1:
            user_input_file = phase_dir / "iteration_001" / "user_input.md"
            if user_input_file.exists():
                compare_content = user_input_file.read_text(encoding="utf-8").strip()
                return current_content != compare_content
        else:
            prev_iteration_dir = phase_dir / f"iteration_{iteration - 1:03d}"
            prev_output_file = prev_iteration_dir / "output.md"
            if prev_output_file.exists():
                compare_content = prev_output_file.read_text(encoding="utf-8").strip()
                return current_content != compare_content

        return True


def ensure_agent_file_exists(agent_name: str, agent_role: str, cafe_dir: Path = Path(".cafe")) -> None:
    """Check if agent md file exists, if not report error and prompt user to reset."""
    from cafe.agents.manager import AgentManager
    from rich.console import Console

    agent_file = cafe_dir / AgentManager.AGENTS_DIR / agent_role / f"{agent_name}.md"

    if not agent_file.exists():
        console = Console()
        console.print(f"[red]✗ Agent file not found: {agent_file}[/red]")
        console.print("[yellow]ℹ Please run 'cafe agent default' to reset default agent files[/yellow]")
        raise FileNotFoundError(
            f"Agent file not found: {agent_file}\n"
            "Run 'cafe agent default' to reset default agent files"
        )
