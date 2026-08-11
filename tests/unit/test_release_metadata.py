"""Release metadata and packaging guardrails."""

import re
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _project_metadata() -> dict[str, object]:
    return tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]


def _dependency_name(requirement: str) -> str:
    return re.split(r"[<>=!~;\[\s]", requirement, maxsplit=1)[0].lower()


def test_direct_runtime_imports_are_declared() -> None:
    metadata = _project_metadata()
    dependency_names = {
        _dependency_name(requirement) for requirement in metadata["dependencies"]
    }

    assert "click" in dependency_names


def test_current_version_has_release_notes_and_migration_guide() -> None:
    version = _project_metadata()["version"]
    changelog = (PROJECT_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    assert f"## [{version}]" in changelog
    assert (PROJECT_ROOT / "docs" / "releases" / f"v{version}.md").is_file()


def test_release_gate_is_executable() -> None:
    release_gate = PROJECT_ROOT / "scripts" / "release-check.sh"

    assert release_gate.is_file()
    assert release_gate.stat().st_mode & 0o111
