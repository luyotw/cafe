"""Interactive menu system for the cafe command.

This module provides the interactive menu that is shown when `cafe` is invoked
without any subcommand. It detects the current project state and presents a
context-aware menu to guide users through common workflows.
"""

import subprocess
import sys
import json
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from rich.console import Console

from cafe.core.git import GitOperations
from cafe.playbooks.loader import PlaybookLoader
from cafe.ui.inquirer_prompts import prompt_list, prompt_text
from cafe.utils.config import ConfigManager
from cafe.utils.crew import CrewManager
from cafe.utils.git_utils import is_branch_initialized


console = Console()


class MenuState(Enum):
    """Represents the current project state for menu selection."""

    NOT_INITIALIZED = "not_initialized"
    NO_ACTIVE_ISSUE = "no_active_issue"
    ACTIVE_ISSUE = "active_issue"


# Sentinel value indicating the user wants to go back to parent menu
_BACK_SENTINEL = "__BACK__"
# Sentinel value indicating the user wants to exit the menu
_EXIT_SENTINEL = "exit"


class MenuStateDetector:
    """Detects the current project state to determine which menu to show."""

    def __init__(self) -> None:
        """Initialize the state detector."""
        self._current_issue_name: Optional[str] = None

    def _config_exists(self) -> bool:
        """Check if .cafe/config.yaml exists in the current directory.

        Returns:
            True if config file exists, False otherwise
        """
        return Path(".cafe/config.yaml").exists()

    def detect_state(self) -> MenuState:
        """Detect the current project state.

        Detection logic:
        1. NOT_INITIALIZED: .cafe/config.yaml does not exist
        2. NO_ACTIVE_ISSUE: config exists, but current branch is not an initialized issue
        3. ACTIVE_ISSUE: config exists and current branch matches an initialized issue

        Returns:
            Current MenuState
        """
        if not self._config_exists():
            self._current_issue_name = None
            return MenuState.NOT_INITIALIZED

        try:
            git = GitOperations()
            branch_name = git.get_current_branch()
            if branch_name and is_branch_initialized(branch_name):
                self._current_issue_name = branch_name
                return MenuState.ACTIVE_ISSUE
        except Exception:
            pass

        self._current_issue_name = None
        return MenuState.NO_ACTIVE_ISSUE

    def get_current_issue_name(self) -> Optional[str]:
        """Get the current issue name (branch name).

        Call detect_state() first to populate this value.

        Returns:
            Current issue name if in ACTIVE_ISSUE state, None otherwise
        """
        return self._current_issue_name


def _run_command(args: List[str]) -> int:
    """Execute a cafe subcommand via subprocess.

    Uses the same pattern as the `cafe make` command.

    Args:
        args: Command arguments, e.g. ["prepare"] or ["config", "edit"]

    Returns:
        Return code from the subprocess
    """
    cmd = [sys.executable, "-m", "cafe.ui.cli"] + args
    try:
        result = subprocess.run(cmd, check=False)
        return result.returncode
    except Exception as e:
        console.print(f"[red]Error running command: {e}[/red]")
        return 1


class InteractiveMenu:
    """Main interactive menu orchestrator.

    Displays a context-aware menu based on the current project state and
    dispatches user selections to the appropriate cafe commands.
    """

    def __init__(
        self,
        state_detector: Optional[MenuStateDetector] = None,
    ) -> None:
        """Initialize the interactive menu.

        Args:
            state_detector: State detector instance (injectable for testing)
        """
        self._detector = state_detector or MenuStateDetector()

    def run(self) -> None:
        """Run the interactive menu loop.

        Detects state on every iteration so the menu automatically
        refreshes after commands complete.
        """
        try:
            while True:
                state = self._detector.detect_state()

                if state == MenuState.ACTIVE_ISSUE:
                    result = self._show_issue_menu()
                else:
                    result = self._show_main_menu(state)

                if result == _EXIT_SENTINEL:
                    break

        except KeyboardInterrupt:
            console.print()

    # ------------------------------------------------------------------
    # Menu builders
    # ------------------------------------------------------------------

    def _build_main_menu_choices(self, state: MenuState) -> List[Dict[str, Any]]:
        """Build the list of choices for the main menu.

        Args:
            state: Current project state

        Returns:
            List of InquirerPy choice dicts with "name" and "value" keys
        """
        choices: List[Dict[str, Any]] = []

        if state == MenuState.NOT_INITIALIZED:
            choices.append({"name": "Init project", "value": "init"})
        else:
            choices += [
                {"name": "New issue", "value": "prepare"},
                {"name": "List issues", "value": "ls"},
                {"name": "Remove issues", "value": "rm"},
                {"name": "Restore archived issue", "value": "restore"},
            ]

        choices += [
            {"name": "Settings", "value": "settings"},
            {"name": "Exit", "value": "exit"},
        ]
        return choices

    def _build_issue_menu_choices(self) -> List[Dict[str, Any]]:
        """Build the list of choices for the issue menu.

        Returns:
            List of InquirerPy choice dicts with "name" and "value" keys
        """
        return [
            {"name": "Continue workflow", "value": "make"},
            {"name": "Chat with agent", "value": "chat"},
            {"name": "Show status", "value": "summary"},
            {"name": "Reset iteration", "value": "reset"},
            {"name": "Close current issue", "value": "close"},
            {"name": "Remove issues", "value": "rm"},
            {"name": "Settings", "value": "settings"},
            {"name": "Exit", "value": "exit"},
        ]

    def _build_settings_menu_choices(self) -> List[Dict[str, Any]]:
        """Build the list of choices for the settings submenu.

        Returns:
            List of InquirerPy choice dicts with "name" and "value" keys
        """
        return [
            {"name": "Agent CLI & model setup", "value": "setup"},
            {"name": "View config", "value": "config"},
            {"name": "Edit config", "value": "config_edit"},
            {"name": "Manage agents", "value": "agent_edit"},
            {"name": "Manage templates", "value": "template_edit"},
            {"name": "Back", "value": "back"},
        ]

    # ------------------------------------------------------------------
    # Menu handlers
    # ------------------------------------------------------------------

    def _show_main_menu(self, state: MenuState) -> str:
        """Display the main menu and handle the user's selection.

        Args:
            state: Current project state

        Returns:
            "exit" if the user wants to quit, otherwise continues the loop
        """
        choices = self._build_main_menu_choices(state)
        selection = prompt_list("CAFE  What would you like to do?", choices)

        if selection == "exit":
            return _EXIT_SENTINEL

        if selection == "settings":
            self._show_settings_menu()
            return ""

        if selection == "init":
            _run_command(["init"])
        elif selection == "prepare":
            _run_command(["prepare"])
        elif selection == "ls":
            _run_command(["ls"])
        elif selection == "rm":
            _run_command(["rm"])
        elif selection == "restore":
            issue_name = prompt_text("Issue name to restore:")
            if issue_name.strip():
                _run_command(["restore", issue_name.strip()])
                return _EXIT_SENTINEL

        return ""

    def _show_issue_menu(self) -> str:
        """Display the issue menu and handle the user's selection.

        Returns:
            "exit" if the user wants to quit, otherwise continues the loop
        """
        issue_name = self._detector.get_current_issue_name()
        title = f"CAFE  [{issue_name}] What would you like to do?"

        choices = self._build_issue_menu_choices()
        selection = prompt_list(title, choices)

        if selection == "exit":
            return _EXIT_SENTINEL

        if selection == "settings":
            self._show_settings_menu()
            return ""

        if selection == "chat":
            self._handle_chat()
        elif selection == "make":
            _run_command(["make"])
        elif selection == "summary":
            _run_command(["summary"])
        elif selection == "reset":
            _run_command(["reset"])
        elif selection == "rm":
            _run_command(["rm"])
        elif selection == "close":
            _run_command(["close"])
            return _EXIT_SENTINEL

        return ""

    def _show_settings_menu(self) -> None:
        """Display the settings submenu and handle the user's selection.

        This menu loops until the user selects "Back".
        """
        while True:
            choices = self._build_settings_menu_choices()
            selection = prompt_list("CAFE  Settings", choices)

            if selection == "back":
                return

            if selection == "setup":
                _run_command(["setup"])
            elif selection == "config":
                _run_command(["config"])
            elif selection == "config_edit":
                _run_command(["config", "edit"])
            elif selection == "agent_edit":
                _run_command(["agent", "edit"])
            elif selection == "template_edit":
                _run_command(["template", "edit"])

    def _get_available_agents(self) -> List[Dict[str, str]]:
        """Get all configured agents.

        Returns all playbook roles that have a configured or default agent,
        regardless of the current workflow phase.

        Returns:
            List of dicts with "role" and "name" keys for all configured agents
        """
        agents: List[Dict[str, str]] = []
        try:
            config_manager = ConfigManager()
            role_names = self._get_playbook_role_names()
            role_defaults = self._get_playbook_role_defaults(role_names)
            crew_data = self._load_crew_data(config_manager)
            for role in role_names:
                name = None
                if role in crew_data and isinstance(crew_data[role], dict):
                    raw_name = crew_data[role].get("name")
                    name = str(raw_name) if raw_name else None
                if not name:
                    name = config_manager.get(f"agents.{role}.name")
                if not name:
                    name = role_defaults.get(role)
                if name:
                    agents.append({"role": role, "name": name})
        except Exception:
            pass
        return agents

    def _get_playbook_role_names(self) -> List[str]:
        issue_name = self._detector.get_current_issue_name()
        playbook_id = "default"
        if issue_name:
            blackboard_file = Path(".cafe") / "issues" / issue_name / "blackboard.json"
            try:
                data = json.loads(blackboard_file.read_text(encoding="utf-8"))
                playbook_id = str(data.get("playbook_id") or "default")
            except Exception:
                playbook_id = "default"

        try:
            playbook = PlaybookLoader(project_root=Path.cwd()).load(playbook_id)
            roles = playbook.get("roles", {})
            if isinstance(roles, dict) and roles:
                return [str(role) for role in roles.keys()]
        except Exception:
            pass
        return ["pm", "developer", "reviewer"]

    def _get_playbook_role_defaults(self, role_names: List[str]) -> Dict[str, str]:
        defaults: Dict[str, str] = {}
        issue_name = self._detector.get_current_issue_name()
        playbook_id = "default"
        if issue_name:
            try:
                data = json.loads((Path(".cafe") / "issues" / issue_name / "blackboard.json").read_text(encoding="utf-8"))
                playbook_id = str(data.get("playbook_id") or "default")
            except Exception:
                playbook_id = "default"
        try:
            playbook = PlaybookLoader(project_root=Path.cwd()).load(playbook_id)
            roles = playbook.get("roles", {})
            for role in role_names:
                role_def = roles.get(role, {}) if isinstance(roles, dict) else {}
                if isinstance(role_def, dict) and role_def.get("default_agent"):
                    defaults[role] = str(role_def["default_agent"])
        except Exception:
            pass
        return defaults

    @staticmethod
    def _load_crew_data(config_manager: ConfigManager) -> Dict[str, Any]:
        try:
            return CrewManager(cafe_dir=Path(config_manager.config_dir)).load()
        except Exception:
            return {}

    def _handle_chat(self) -> None:
        """Prompt user to select an agent role then launch cafe chat."""
        agents = self._get_available_agents()

        if not agents:
            console.print("[yellow]No agents configured. Run 'cafe setup' first.[/yellow]")
            return

        role_choices = [
            {
                "name": f"{a['role'].capitalize():<12} ({a['name']})",
                "value": a["role"],
            }
            for a in agents
        ]
        role_choices.append({"name": "Back", "value": "back"})

        selection = prompt_list("CAFE  Chat with agent  Select role:", role_choices)
        if selection == "back":
            return

        _run_command(["chat", selection])
