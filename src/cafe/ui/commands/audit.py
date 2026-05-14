"""``cafe audit`` — validate builtin skills and playbooks."""

from __future__ import annotations

import sys

import typer

from cafe.audit.tooling_audit import format_audit_markdown, run_builtin_tooling_audit


def audit() -> None:
    """Audit builtin skills and playbooks (agents, hooks, scripts, intents, skill placeholders)."""
    lines = run_builtin_tooling_audit()
    # Avoid Rich interpreting ``[x]`` checklist markers as markup.
    print(format_audit_markdown(lines).rstrip("\n"))
    if not all(row.ok for row in lines):
        raise typer.Exit(code=1)


def main() -> None:
    """Entry point for ``python -m cafe.ui.commands.audit``."""
    try:
        audit()
    except typer.Exit as exc:
        sys.exit(int(exc.exit_code))


if __name__ == "__main__":
    main()
