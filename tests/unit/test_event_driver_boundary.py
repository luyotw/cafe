"""Ensure workflow role policy stays within its role-owned package and skill."""

from __future__ import annotations

import re
from pathlib import Path


def test_cafe_core_has_no_manager_mode_implementation() -> None:
    source_root = Path(__file__).parents[2] / "src" / "cafe"
    allowed_roots = (
        source_root / "data" / "skills" / "use-cafe-workflow",
        source_root / "driver",
        source_root / "manager",
    )
    forbidden = (
        "DriverPolicy",
        "driver_policy",
        "driver_state",
        "ManagerPolicy",
        "manager_policy",
        "manager_state",
        "delegated",
    )
    offenders: list[str] = []

    for path in source_root.rglob("*.py"):
        if any(path.is_relative_to(root) for root in allowed_roots):
            continue
        text = path.read_text(encoding="utf-8")
        if any(token in text for token in forbidden):
            offenders.append(str(path.relative_to(source_root)))

    assert offenders == []


def test_event_manager_status_projection_stays_in_the_skill_boundary() -> None:
    source_root = Path(__file__).parents[2] / "src" / "cafe"
    callback = (
        source_root
        / "data"
        / "skills"
        / "use-cafe-workflow"
        / "scripts"
        / "workflow_event_callback.py"
    )

    assert "def read_status(" in callback.read_text(encoding="utf-8")
    for path in (source_root / "core").rglob("*.py"):
        assert "read_status" not in path.read_text(encoding="utf-8")


def test_manager_contract_application_has_only_role_owned_production_boundaries() -> None:
    """Generic core, runtime, phases, and UI stay independent of role authority."""
    source_root = Path(__file__).parents[2] / "src" / "cafe"
    driver_root = source_root / "driver"
    manager_root = source_root / "manager"
    role_roots = (driver_root, manager_root)
    skill_root = source_root / "data" / "skills" / "use-cafe-workflow"
    importers: list[Path] = []

    for path in source_root.rglob("*.py"):
        if any(path.is_relative_to(root) for root in role_roots):
            continue
        source = path.read_text(encoding="utf-8")
        if re.search(r"^\s*(?:from|import)\s+cafe\.(?:driver|manager)\b", source, flags=re.MULTILINE):
            importers.append(path)
        if not path.is_relative_to(skill_root):
            assert "driver/contract.json" not in source
            assert "manager/contract.json" not in source

    assert importers
    assert all(path.is_relative_to(skill_root) for path in importers)


def test_role_contracts_have_no_generic_configuration_bridge() -> None:
    """Manager authority does not import generic workflow or PR policy."""
    source_root = Path(__file__).parents[2] / "src" / "cafe"
    role_roots = (source_root / "driver", source_root / "manager")
    skill_root = source_root / "data" / "skills" / "use-cafe-workflow"
    forbidden = (
        "pr_auto_create",
        "cafe.pr.publish",
        "generic_inputs",
        "issue.yaml",
        "phases.yaml",
        "semantic_fingerprint",
        "repository_content",
    )

    for role_root in role_roots:
        for path in role_root.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            if path.name == "_lifecycle.py":
                assert not any(token in source for token in ("generic_inputs", "issue.yaml", "phases.yaml")), path
                continue
            assert not any(token in source for token in forbidden), path

    assert not (skill_root / "scripts" / "run_validated_driver_workflow.py").exists()
    assert not (skill_root / "scripts" / "run_validated_manager_workflow.py").exists()

    for path in source_root.rglob("*.py"):
        if path.is_relative_to(skill_root) or any(path.is_relative_to(root) for root in role_roots):
            continue
        source = path.read_text(encoding="utf-8")
        assert "cafe.driver" not in source
        assert "cafe.manager" not in source
        assert "driver/contract.json" not in source
        assert "manager/contract.json" not in source
