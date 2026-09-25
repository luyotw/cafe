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
    snapshot_script_tree,
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


def test_tree_snapshot_rejects_runtime_directory_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "skills"
    skill = root / "cafe-plan"
    scripts = skill / "scripts"
    scripts.mkdir(parents=True)
    script = scripts / "hook.sh"
    script.write_text("#!/bin/sh\necho safe\n", encoding="utf-8")
    attacker = tmp_path / "attacker"
    (attacker / "scripts").mkdir(parents=True)
    (attacker / "scripts" / "hook.sh").write_text("#!/bin/sh\necho attacker\n", encoding="utf-8")
    displaced = root / "displaced"
    native_open = os.open
    swapped = False

    def replace_skill_directory(path, flags, *args, **kwargs):
        nonlocal swapped
        if not swapped and path == "cafe-plan":
            swapped = True
            skill.rename(displaced)
            attacker.rename(skill)
        return native_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", replace_skill_directory)

    with pytest.raises(ValueError):
        snapshot_script_tree(script, allowed_root=root)
    assert swapped is True


def test_tree_snapshot_contains_only_declared_entries_and_hashes_the_runtime_closure(
    tmp_path: Path,
) -> None:
    catalog = tmp_path / "catalog"
    phase = catalog / "phase"
    shared = tmp_path / "global" / "shared"
    undeclared = catalog / "undeclared"
    (phase / "scripts").mkdir(parents=True)
    (shared / "scripts").mkdir(parents=True)
    (undeclared / "scripts").mkdir(parents=True)
    script = phase / "scripts" / "hook.sh"
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    shared_resource = shared / "scripts" / "message.txt"
    shared_resource.write_text("first\n", encoding="utf-8")
    (undeclared / "scripts" / "secret.txt").write_text("secret\n", encoding="utf-8")

    first = snapshot_script_tree(
        script,
        allowed_root=catalog,
        runtime_entries={"phase": phase, "shared": shared},
    )
    try:
        assert (first.root / "phase" / "scripts" / "hook.sh").is_file()
        assert (first.root / "shared" / "scripts" / "message.txt").is_file()
        assert not (first.root / "undeclared").exists()
        first_digest = first.digest
    finally:
        first.cleanup()

    shared_resource.write_text("second\n", encoding="utf-8")
    second = snapshot_script_tree(
        script,
        allowed_root=catalog,
        runtime_entries={"phase": phase, "shared": shared},
    )
    try:
        assert second.digest != first_digest
    finally:
        second.cleanup()


@pytest.mark.parametrize(
    ("tree_builder", "limits"),
    [
        (
            lambda root: [
                (root / "extra-a").write_text("a", encoding="utf-8"),
                (root / "extra-b").write_text("b", encoding="utf-8"),
            ],
            {"max_files": 2},
        ),
        (
            lambda root: (root / "large.txt").write_text("oversized", encoding="utf-8"),
            {"max_bytes": 8},
        ),
        (
            lambda root: (root / "one" / "two" / "three").mkdir(parents=True),
            {"max_depth": 2},
        ),
    ],
)
def test_tree_snapshot_rejects_runtime_closure_over_resource_limits(
    tmp_path: Path, tree_builder, limits: dict[str, int]
) -> None:
    catalog = tmp_path / "catalog"
    skill = catalog / "phase"
    skill.mkdir(parents=True)
    script = skill / "hook.sh"
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    tree_builder(skill)

    with pytest.raises(ValueError, match="limit"):
        snapshot_script_tree(script, allowed_root=catalog, **limits)


def test_script_launcher_inventory_covers_workflow_process_calls() -> None:
    root = Path(__file__).resolve().parents[2]
    inventory = (root / "docs" / "script-execution-boundaries.md").read_text(encoding="utf-8")
    documented = Counter()
    classifications: dict[str, str] = {}
    inventory_rows = re.findall(
        r"^\| `([^`]+\.py::[^`]+)`(?: ×(\d+))? \| ([^|]+) \|$",
        inventory,
        flags=re.MULTILINE,
    )
    for identity, count, classification in inventory_rows:
        assert identity not in classifications, f"duplicate launcher inventory row: {identity}"
        documented[identity] += int(count or "1")
        classifications[identity] = classification.strip()

    required_classifications = {
        "src/cafe/core/capabilities.py::run_pr_publish_capability": (
            "Registered host capability adapter"
        ),
        "src/cafe/core/sandbox_execution.py::preflight_sandbox": (
            "Internal fixed sandbox preflight"
        ),
        "src/cafe/core/sandbox_execution.py::run": "Sandbox script adapter",
        "src/cafe/data/skills/use-cafe-workflow/scripts/run_workflow.py::run": (
            "Internal fixed CAFE workflow bootstrap"
        ),
        "src/cafe/data/skills/use-cafe-workflow/scripts/execute_closeout.py::main": (
            "Explicit confirmed closeout command adapter"
        ),
        "src/cafe/verification/receipt.py::_run_with_output_log": ("Explicit verification runner"),
        "src/cafe/verification/receipt.py::run_focused_verification": (
            "Explicit verification runner"
        ),
    }
    assert {
        identity: classifications.get(identity) for identity in required_classifications
    } == required_classifications

    discovered = Counter()
    for path in (root / "src" / "cafe").rglob("*.py"):
        relative = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))

        def is_process_callable(
            node: ast.expr,
            process_modules: dict[str, str],
            process_callables: set[str],
        ) -> bool:
            if isinstance(node, ast.Name):
                return node.id in process_callables
            if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
                return False
            module = process_modules.get(node.value.id)
            return (module == "subprocess" and node.attr in {"run", "Popen"}) or (
                module == "os" and node.attr.startswith("exec")
            )

        def scope_nodes(body: list[ast.stmt]) -> list[ast.AST]:
            pending: list[ast.AST] = list(reversed(body))
            found: list[ast.AST] = []
            while pending:
                node = pending.pop()
                found.append(node)
                if isinstance(
                    node,
                    (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda),
                ):
                    continue
                pending.extend(reversed(list(ast.iter_child_nodes(node))))
            return found

        def collect_scope_bindings(
            body: list[ast.stmt],
            process_modules: dict[str, str],
            process_callables: set[str],
        ) -> None:
            nodes = scope_nodes(body)
            for node in nodes:
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

            changed = True
            while changed:
                changed = False
                for node in nodes:
                    if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                        continue
                    value = node.value
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    if not is_process_callable(value, process_modules, process_callables):
                        continue
                    for target in targets:
                        if isinstance(target, ast.Name) and target.id not in process_callables:
                            process_callables.add(target.id)
                            changed = True

        module_process_modules: dict[str, str] = {}
        module_process_callables: set[str] = set()
        collect_scope_bindings(
            tree.body,
            module_process_modules,
            module_process_callables,
        )

        def function_bindings(
            node: ast.FunctionDef | ast.AsyncFunctionDef,
            parent_modules: dict[str, str],
            parent_callables: set[str],
        ) -> tuple[dict[str, str], set[str]]:
            process_modules = dict(parent_modules)
            process_callables = set(parent_callables)
            positional = [*node.args.posonlyargs, *node.args.args]
            default_pairs = zip(positional[-len(node.args.defaults) :], node.args.defaults)
            for argument, default in default_pairs:
                if is_process_callable(default, parent_modules, parent_callables):
                    process_callables.add(argument.arg)
            for argument, default in zip(node.args.kwonlyargs, node.args.kw_defaults):
                if default is not None and is_process_callable(
                    default, parent_modules, parent_callables
                ):
                    process_callables.add(argument.arg)
            collect_scope_bindings(node.body, process_modules, process_callables)
            return process_modules, process_callables

        class ScopedVisitor(ast.NodeVisitor):
            def __init__(self) -> None:
                self.process_modules = module_process_modules
                self.process_callables = module_process_callables
                self.scope: list[str] = []

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                previous = self.process_modules, self.process_callables
                self.process_modules, self.process_callables = function_bindings(node, *previous)
                self.scope.append(node.name)
                for child in node.body:
                    self.visit(child)
                self.scope.pop()
                self.process_modules, self.process_callables = previous

            def visit_AsyncFunctionDef(  # noqa: N802
                self, node: ast.AsyncFunctionDef
            ) -> None:
                self.visit_FunctionDef(node)

        runner_attributes: set[str] = set()

        class RunnerAttributeVisitor(ScopedVisitor):
            def visit_Assign(self, node: ast.Assign) -> None:
                self._record(node.targets, node.value)
                self.generic_visit(node)

            def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
                if node.value is not None:
                    self._record([node.target], node.value)
                self.generic_visit(node)

            def _record(self, targets: list[ast.expr], value: ast.expr) -> None:
                if not is_process_callable(value, self.process_modules, self.process_callables):
                    return
                for target in targets:
                    if (
                        isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "self"
                    ):
                        runner_attributes.add(target.attr)

        RunnerAttributeVisitor().visit(tree)

        class Visitor(ScopedVisitor):
            def visit_Call(self, node: ast.Call) -> None:
                target = node.func
                injected_runner = (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "self"
                    and target.attr in runner_attributes
                )
                if (
                    is_process_callable(target, self.process_modules, self.process_callables)
                    or injected_runner
                ):
                    discovered[f"{relative}::{'.'.join(self.scope) or '<module>'}"] += 1
                self.generic_visit(node)

        Visitor().visit(tree)

    assert "src/cafe/core/sandbox_execution.py::run" in discovered
    assert "src/cafe/verification/receipt.py::_run_with_output_log" in discovered
    assert discovered == documented
