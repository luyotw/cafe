"""InquirerPy wrapper functions for consistent interactive prompts.

This module wraps InquirerPy functionality, providing a unified interface for CLI commands.
"""

from typing import Any, Optional

from InquirerPy import inquirer


def prompt_list(
    message: str,
    choices: list[Any],
    default: Optional[Any] = None,
) -> Any:
    """Display menu for user to select

    Args:
        message: Prompt message
        choices: List of choices
        default: Default option

    Returns:
        User's selected item
    """
    kwargs = {
        "message": message,
        "choices": choices,
    }
    if default is not None:
        kwargs["default"] = default

    return inquirer.select(**kwargs).execute()


def prompt_text(
    message: str,
    default: str = "",
) -> str:
    """Prompt user for text input

    Args:
        message: Prompt message
        default: Default value

    Returns:
        User's input text
    """
    return inquirer.text(
        message=message,
        default=default,
    ).execute()


def prompt_multiline(
    message: str,
    default: str = "",
    preamble: Optional[str] = None,
) -> str:
    """Prompt user for multiline text input

    Args:
        message: Prompt message
        default: Default value
        preamble: Optional text printed before the prompt

    Returns:
        User's input text (multiline)
    """
    if preamble:
        print(preamble)

    return inquirer.text(
        message=message,
        default=default,
        multiline=True,
        instruction="(Press Esc + Enter to finish)",
    ).execute()


def prompt_confirm(
    message: str,
    default: bool = True,
) -> bool:
    """Ask user for confirmation (Yes/No)

    Args:
        message: Prompt message
        default: Default value (True or False)

    Returns:
        User's choice (True or False)
    """
    return inquirer.confirm(
        message=message,
        default=default,
    ).execute()
