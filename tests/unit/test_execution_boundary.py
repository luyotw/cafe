import ast
import os
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
        ScriptLaunchRequest.model_validate(
            {"script": tmp_path / "hook.sh", "boundary": _boundary(tmp_path)}
        )

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


def test_snapshot_rejects_ancestor_swap_during_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "skills"
    ancestor = root / "nested"
    ancestor.mkdir(parents=True)
    script = ancestor / "hook.sh"
    script.write_text("#!/bin/sh\necho safe\n", encoding="utf-8")
    attacker = tmp_path / "attacker"
    attacker.mkdir()
    (attacker / "hook.sh").write_text("#!/bin/sh\necho attacker\n", encoding="utf-8")
    displaced = root / "displaced"
    native_open = os.open
    swapped = False

    def swap_ancestor(path, flags, *args, **kwargs):
        nonlocal swapped
        if not swapped and str(path) in {"nested", str(script)}:
            swapped = True
            ancestor.rename(displaced)
            ancestor.symlink_to(attacker, target_is_directory=True)
        return native_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swap_ancestor)

    with pytest.raises((OSError, ValueError)):
        snapshot_script(script, allowed_root=root)
    assert swapped is True


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

        process_modules = {"subprocess": "subprocess", "os": "os"}
        process_callables: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in {"subprocess", "os"}:
                        process_modules[alias.asname or alias.name] = alias.name
            elif isinstance(node, ast.ImportFrom) and node.module in {"subprocess", "os"}:
                for alias in node.names:
                    if (node.module == "subprocess" and alias.name in {"run", "Popen"}) or (
                        node.module == "os" and alias.name.startswith("exec")
                    ):
                        process_callables.add(alias.asname or alias.name)

        def is_process_callable(node: ast.expr) -> bool:
            if isinstance(node, ast.Name):
                return node.id in process_callables
            if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
                return False
            module = process_modules.get(node.value.id)
            return (module == "subprocess" and node.attr in {"run", "Popen"}) or (
                module == "os" and node.attr.startswith("exec")
            )

        changed = True
        while changed:
            changed = False
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                    continue
                value = node.value
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if not is_process_callable(value):
                    continue
                for target in targets:
                    if isinstance(target, ast.Name) and target.id not in process_callables:
                        process_callables.add(target.id)
                        changed = True

        runner_attributes: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            positional = [*node.args.posonlyargs, *node.args.args]
            default_names = {
                argument.arg
                for argument, default in zip(
                    positional[-len(node.args.defaults) :], node.args.defaults
                )
                if is_process_callable(default)
            }
            default_names.update(
                argument.arg
                for argument, default in zip(node.args.kwonlyargs, node.args.kw_defaults)
                if default is not None and is_process_callable(default)
            )
            for child in ast.walk(node):
                if not isinstance(child, (ast.Assign, ast.AnnAssign)):
                    continue
                value = child.value
                targets = child.targets if isinstance(child, ast.Assign) else [child.target]
                if not isinstance(value, ast.Name) or value.id not in default_names:
                    continue
                for target in targets:
                    if (
                        isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "self"
                    ):
                        runner_attributes.add(target.attr)

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
                injected_runner = (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                    and target.attr in runner_attributes
                )
                if is_process_callable(target) or injected_runner:
                    discovered[f"{relative}::{'.'.join(self.scope) or '<module>'}"] += 1
                self.generic_visit(node)

        Visitor().visit(tree)

    assert "src/cafe/core/sandbox_execution.py::run" in discovered
    assert discovered == documented
