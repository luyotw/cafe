"""Compose the builtin Manager command toward the generic CAFE CLI."""
from __future__ import annotations

import copy
import importlib.util
import sys
from functools import wraps
from importlib.resources import files
from pathlib import Path

import typer

from cafe.ui import cli as generic_cli
from cafe.ui.commands import lifecycle

# Retain generic command registrations and dynamic routing without mutating its app.
app = copy.copy(generic_cli.app)
app.registered_commands = [
    command for command in generic_cli.app.registered_commands if command.callback is not lifecycle.close
]
app.registered_groups = list(generic_cli.app.registered_groups)
manager_app = typer.Typer(help='Talk to the current issue workflow Manager.')
app.add_typer(manager_app, name='manager')


@app.command('close')
@wraps(lifecycle.close)
def close(archive_only, issue):
    from . import local_cleanup

    lifecycle._close(archive_only, issue, local_authority=local_cleanup)


def _builtin_chat():
    key = '_cafe_builtin_manager_chat'
    if key not in sys.modules:
        path = files('cafe').joinpath('data/skills/use-cafe-workflow/scripts/manager_chat.py')
        spec = importlib.util.spec_from_file_location(key, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(key, None)
            raise
    return sys.modules[key]


@manager_app.command('chat')
def chat(issue: str | None = typer.Option(None, '--issue', help='Select a live issue explicitly.')) -> None:
    """Reconnect to the current verified Manager in the CAFE terminal."""
    raise typer.Exit(_builtin_chat().run_chat(Path.cwd(), issue))


def main() -> int | None:
    return generic_cli.main(application=app, entry_module=__name__ if __name__ != '__main__' else 'cafe.manager.cli')


if __name__ == '__main__':
    sys.exit(main() or 0)
