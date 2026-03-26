"""Interactive menu system for the cafe command.

This module provides the interactive menu that is shown when `cafe` is invoked
without any subcommand. It detects the current project state and presents a
context-aware menu to guide users through common workflows.
"""

import subprocess
import sys
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from rich.console import Console

from cafe.core.git import GitOperations
from cafe.ui.inquirer_prompts import prompt_list
from cafe.utils.config import ConfigManager
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
            choices.append({
                "name": "Init project          Initialize CAFE in this project",
                "value": "init",
            })
        else:
            choices += [
                {
                    "name": "New issue             Create a new issue",
                    "value": "prepare",
                },
                {
                    "name": "List issues           Show all issues",
                    "value": "ls",
                },
                {
                    "name": "Restore issue         Restore an archived issue",
                    "value": "restore",
                },
            ]

        choices += [
            {
                "name": "Settings              Configure agents, templates and more",
                "value": "settings",
            },
            {
                "name": "Exit",
                "value": "exit",
            },
        ]
        return choices

    def _build_issue_menu_choices(self) -> List[Dict[str, Any]]:
        """Build the list of choices for the issue menu.

        Returns:
            List of InquirerPy choice dicts with "name" and "value" keys
        """
        return [
            {
                "name": "Continue workflow      Run the next workflow phase",
                "value": "make",
            },
            {
                "name": "Chat with agent       Open interactive chat with an agent",
                "value": "chat",
            },
            {
                "name": "Show status           Display workflow timeline",
                "value": "summary",
            },
            {
                "name": "Reset iteration       Remove last iteration",
                "value": "reset",
            },
            {
                "name": "Close issue           Close issue and return to base branch",
                "value": "close",
            },
            {
                "name": "Settings              Configure agents, templates and more",
                "value": "settings",
            },
            {
                "name": "Exit",
                "value": "exit",
            },
        ]

    def _build_settings_menu_choices(self) -> List[Dict[str, Any]]:
        """Build the list of choices for the settings submenu.

        Returns:
            List of InquirerPy choice dicts with "name" and "value" keys
        """
        return [
            {
                "name": "Agent setup           Configure agent roles",
                "value": "setup",
            },
            {
                "name": "View config           Show current configuration",
                "value": "config",
            },
            {
                "name": "Edit config           Open config in editor",
                "value": "config_edit",
            },
            {
                "name": "Manage agents         List and manage agents",
                "value": "agent_ls",
            },
            {
                "name": "Manage templates      List and manage templates",
                "value": "template_ls",
            },
            {
                "name": "Back                  Return to previous menu",
                "value": "back",
            },
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
        elif selection == "restore":
            _run_command(["restore"])

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
        elif selection == "close":
            _run_command(["close"])

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
            elif selection == "agent_ls":
                _run_command(["agent", "ls"])
            elif selection == "template_ls":
                _run_command(["template", "ls"])

    def _get_available_agents(self) -> List[Dict[str, str]]:
        """Get all configured agents.

        Returns all agents (pm, developer, reviewer) that have been configured,
        regardless of the current workflow phase.

        Returns:
            List of dicts with "role" and "name" keys for all configured agents
        """
        agents: List[Dict[str, str]] = []
        try:
            config_manager = ConfigManager()
            for role in ("pm", "developer", "reviewer"):
                name = config_manager.get(f"agents.{role}.name")
                if name:
                    agents.append({"role": role, "name": name})
        except Exception:
            pass
        return agents

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
