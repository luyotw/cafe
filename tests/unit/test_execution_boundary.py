import ast
import re
from collections import Counter
from pathlib import Path

import pytest

from cafe.core.execution_boundary import (
    EffectiveBoundary,
    ExecutionClass,
    ExecutionReceipt,
    ScriptLaunchRequest,
    TrustSource,
    snapshot_script,
)


def _boundary(tmp_path: Path) -> EffectiveBoundary:
    return EffectiveBoundary(
        cwd=tmp_path,
        readable_roots=(tmp_path,),
        writable_roots=(tmp_path,),
        network_destinations=(),
        environment={"PATH": "/usr/bin", "TOKEN": "sentinel"},
    )


def test_execution_class_is_mandatory_and_reference_cannot_promote_trust(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        ScriptLaunchRequest.model_validate({"script": tmp_path / "hook.sh", "boundary": _boundary(tmp_path)})

    request = ScriptLaunchRequest(
        execution_class=ExecutionClass.SANDBOX,
        trust_source=TrustSource.WORKFLOW,
        script=tmp_path / "hook.sh",
        boundary=_boundary(tmp_path),
    )
    changed = request.model_copy(update={"script": tmp_path / "overrides" / "hook.sh"})
    assert changed.execution_class is ExecutionClass.SANDBOX
    assert changed.trust_source is TrustSource.WORKFLOW


def test_boundary_constructs_environment_and_receipt_redacts_secrets(tmp_path: Path) -> None:
    boundary = _boundary(tmp_path)
    assert boundary.environment == {"PATH": "/usr/bin"}
    receipt = ExecutionReceipt(
        correlation_id="correlation",
        execution_class=ExecutionClass.SANDBOX,
        trust_source=TrustSource.WORKFLOW,
        outcome="denied",
        boundary=boundary,
        details={"api_token": "sentinel", "reason": "blocked"},
    )
    dumped = receipt.model_dump(mode="json")
    assert "sentinel" not in str(dumped)
    assert dumped["details"]["api_token"] == "[REDACTED]"


def test_snapshot_rejects_symlinks_and_is_immune_to_target_replacement(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    script = root / "hook.sh"
    script.write_text("#!/bin/sh\necho safe\n", encoding="utf-8")
    snap = snapshot_script(script, allowed_root=root)
    script.write_text("#!/bin/sh\necho attacker\n", encoding="utf-8")
    assert snap.path.read_text(encoding="utf-8").endswith("echo safe\n")
    link = root / "linked.sh"
    link.symlink_to(script)
    with pytest.raises(ValueError):
        snapshot_script(link, allowed_root=root)
    snap.cleanup()


def test_script_launcher_inventory_covers_workflow_process_calls() -> None:
    root = Path(__file__).resolve().parents[2]
    inventory = (root / "docs" / "script-execution-boundaries.md").read_text(encoding="utf-8")
    documented = Counter()
    for identity, count in re.findall(r"`([^`]+\.py::[^`]+)`(?: ×(\d+))?", inventory):
        documented[identity] += int(count or "1")

    discovered = Counter()
    for path in (root / "src" / "cafe").rglob("*.py"):
        relative = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))

        class Visitor(ast.NodeVisitor):
            def __init__(self) -> None:
                self.scope: list[str] = []

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                self.scope.append(node.name)
                self.generic_visit(node)
                self.scope.pop()

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_Call(self, node: ast.Call) -> None:
                target = node.func
                owner = target.value.id if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) else ""
                name = target.attr if isinstance(target, ast.Attribute) else ""
                if (owner == "subprocess" and name in {"run", "Popen"}) or (owner == "os" and name.startswith("exec")):
                    discovered[f"{relative}::{'.'.join(self.scope) or '<module>'}"] += 1
                self.generic_visit(node)

        Visitor().visit(tree)

    assert discovered == documented
