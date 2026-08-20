from pathlib import Path

import pytest
import typer

from cafe.core.lifecycle_trust import (
    LifecycleDeclaration,
    LifecycleTrustStore,
    declare_lifecycle_trust,
)
from cafe.ui.commands import trust as trust_command


def test_lifecycle_declaration_binds_identity_stage_and_scope(tmp_path: Path) -> None:
    script = tmp_path / "close.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    store = LifecycleTrustStore(tmp_path / "home" / ".cafe" / "lifecycle-trust.yaml")
    declaration = declare_lifecycle_trust(store, script=script, stage="close", cwd=tmp_path, writable_roots=(tmp_path,))
    assert store.get(declaration.id) == declaration
    script.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    assert store.validate_identity(declaration) is False
    assert oct(store.path.stat().st_mode & 0o777) == "0o600"


def test_lifecycle_trust_requires_interactive_terminal(tmp_path: Path, monkeypatch) -> None:
    script = tmp_path / "prepare.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    monkeypatch.setattr(typer, "confirm", lambda *_args, **_kwargs: pytest.fail("must deny before prompting"))

    with pytest.raises(typer.Abort):
        trust_command.lifecycle(script, stage="prepare", cwd=tmp_path, write=[tmp_path])


def test_lifecycle_namespace_rejects_capability_impersonation(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        LifecycleDeclaration(
            id="cafe.pr.publish", script=tmp_path / "x", digest="0" * 64,
            stages=("close",), cwd=tmp_path, writable_roots=(tmp_path,),
        )
    with pytest.raises(ValueError):
        LifecycleDeclaration.model_validate({
            "id": "local-close", "script": tmp_path / "x", "digest": "0" * 64,
            "stages": ["close"], "cwd": tmp_path, "writable_roots": [tmp_path],
            "credentials": ["token"],
        })
