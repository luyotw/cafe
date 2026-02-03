"""Shared Phase UI interaction prompt functions

This module provides reusable UI interaction functions for spec/plan phases and prepare commands.
"""

from pathlib import Path
from typing import Optional, Any, Dict, List, Tuple
import yaml

from cafe.core.types import SpecRigor
from cafe.ui.display import Display
from cafe.ui.inquirer_prompts import prompt_list, prompt_text, prompt_confirm
from cafe.utils.github import GitHubOps, GitHubError
from cafe.utils.git_utils import is_github_repo


def prompt_for_input_method(display: Display, github_ops: GitHubOps) -> tuple[str, Optional[int]]:
    """Ask user to select input method (manual vs GitHub Issue)

    Args:
        display: Display instance (not currently used, but kept for future extension)
        github_ops: GitHubOps instance for validating Issue ID

    Returns:
        Tuple of (method, issue_id):
        - method: "manual" or "github"
        - issue_id: Issue ID (int) if GitHub is selected, None otherwise
    """
    choices = [
        "1. Manual input",
        "2. Fetch from GitHub Issue",
    ]

    choice = prompt_list(
        message="Please select input method:",
        choices=choices,
    )

    if choice.startswith("1"):
        return ("manual", None)
    else:
        # GitHub Issue mode
        print()
        print("⚠️  Note: Upon completion, spec.md will be posted back to GitHub Issue as a comment")
        print()

        # Ask for Issue ID or URL, with error retry handling
        while True:
            issue_input = prompt_text(
                message="Please enter GitHub Issue ID or URL:",
                default="",
            )

            try:
                # Use GitHubOps to extract issue number
                issue_id_str = github_ops.extract_issue_number(issue_input)
                issue_id = int(issue_id_str)

                print()
                print(f"✓ Will fetch requirements from GitHub Issue #{issue_id}")
                print()

                return ("github", issue_id)
            except (ValueError, GitHubError) as e:
                print(f"❌ Invalid Issue ID or URL: {e}")
                print("Please try again...")
                print()


def prompt_for_rigor(display: Display) -> str:
    """Ask for specification rigor level

    Args:
        display: Display instance (not currently used, but kept for future extension)

    Returns:
        Rigor level string: 'low' | 'medium' | 'high'
    """
    choices = [
        "Low - Fast development mode\n   • Ask only critical information\n   • Allow ambiguity, let developers decide\n   • Suitable for: rapid prototypes, MVP, internal tools",
        "Medium - Balanced mode [Default]\n   • Ask important details and key scenarios\n   • Balance speed and precision\n   • Suitable for: general feature development",
        "High - Precise specification mode\n   • Ask all details and edge cases\n   • Ensure requirements are testable, no ambiguity\n   • Suitable for: core features, API design, external products",
    ]

    choice = prompt_list(
        message="Please select specification rigor level:",
        choices=choices,
        default=choices[1],  # Default to Medium
    )

    # Parse the choice to get rigor level
    if choice.startswith("Low"):
        print(f"✓ Selected: Low - Fast development mode\n")
        return "low"
    elif choice.startswith("High"):
        print(f"✓ Selected: High - Precise specification mode\n")
        return "high"
    else:
        print(f"✓ Selected: Medium - Balanced mode\n")
        return "medium"


def fetch_github_issue(github_ops: GitHubOps, issue_id: int) -> Tuple[str, List[str]]:
    """Fetch GitHub Issue content

    Args:
        github_ops: GitHubOps instance
        issue_id: GitHub issue ID

    Returns:
        Tuple of (content, image_urls):
        - content: Requirement text combining title and body
        - image_urls: List of image URLs extracted from issue body

    Raises:
        GitHubError: Failed to fetch issue
        RuntimeError: gh CLI not authenticated
    """
    # Check gh CLI authentication status
    if not github_ops.check_gh_auth():
        raise RuntimeError("gh CLI is not authenticated. Please run: gh auth login")

    issue_data = github_ops.get_issue(str(issue_id), include_comments=False)

    # Combine title and body as requirement content
    issue_title = issue_data.get("title", "")
    issue_body = issue_data.get("body", "")
    fetched_content = f"# {issue_title}\n\n{issue_body}" if issue_title else issue_body

    # Extract image URLs from issue body
    image_urls = github_ops.extract_image_urls(issue_body) if issue_body else []

    return fetched_content, image_urls


def prompt_and_save_auto_create(config_file: Path, config_key: str) -> bool:
    """Prompt user for auto_create setting and save to config file

    Detect GitHub repository context and only prompt user when applicable.
    Save user's choice to issue config file.

    Args:
        config_file: Path to issue.yaml file
        config_key: Configuration key name (e.g., "pr.auto_create")

    Returns:
        bool: True if user wants automatic creation, False otherwise
    """
    from rich.console import Console

    console = Console()
    console.print()

    if is_github_repo():
        auto_create = prompt_confirm(
            "Automatically create PR on GitHub after development?", default=True
        )
    else:
        # Non-GitHub repo: disable auto creation
        auto_create = False

    # Save the choice to issue config
    try:
        if config_file.exists():
            with open(config_file, 'r', encoding='utf-8') as f:
                config_data = yaml.safe_load(f) or {}
        else:
            config_data = {}
    except Exception:
        config_data = {}

    # Support dot notation for nested keys (e.g., "pr.auto_create")
    if '.' in config_key:
        keys = config_key.split('.')
        current = config_data
        for key in keys[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]
        current[keys[-1]] = auto_create
    else:
        config_data[config_key] = auto_create

    # Write back to config file
    config_file.parent.mkdir(parents=True, exist_ok=True)
    with open(config_file, 'w', encoding='utf-8') as f:
        yaml.dump(config_data, f, default_flow_style=False, allow_unicode=True)

    return auto_create
