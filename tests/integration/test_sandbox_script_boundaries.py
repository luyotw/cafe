import os
import subprocess
from pathlib import Path

from cafe.core.execution_boundary import EffectiveBoundary, ExecutionClass, ScriptLaunchRequest, TrustSource
from cafe.core.sandbox_execution import SandboxExecutor


def test_custom_override_runs_from_snapshot_without_ambient_authority(tmp_path: Path) -> None:
    script = tmp_path / "project-skill" / "hook.sh"
    script.parent.mkdir()
    script.write_text("#!/bin/sh\necho safe\n", encoding="utf-8")
    observed = {}
    def runner(command, **kwargs):
        script.write_text("#!/bin/sh\necho attacker\n", encoding="utf-8")
        observed["snapshot"] = Path(command[-1]).read_text(encoding="utf-8")
        observed["env"] = kwargs["env"]
        return subprocess.CompletedProcess(command, 0, "safe", "")
    request = ScriptLaunchRequest(
        execution_class=ExecutionClass.SANDBOX, trust_source=TrustSource.WORKFLOW,
        script=script,
        boundary=EffectiveBoundary(cwd=tmp_path, readable_roots=(tmp_path,), writable_roots=(tmp_path,), environment={"PATH": os.environ.get("PATH", ""), "GH_TOKEN": "sentinel", "HTTPS_PROXY": "sentinel"}),
    )
    result = SandboxExecutor(codex_path="codex", runner=runner).run(request)
    assert result.receipt.outcome == "success"
    assert "safe" in observed["snapshot"] and "attacker" not in observed["snapshot"]
    assert "GH_TOKEN" not in observed["env"] and "HTTPS_PROXY" not in observed["env"]
    assert result.receipt.trust_source is TrustSource.WORKFLOW


def test_symlink_override_is_denied_before_sandbox_process_launch(tmp_path: Path) -> None:
    target = tmp_path / "target.sh"
    target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    link = tmp_path / "hook.sh"
    link.symlink_to(target)
    called = False
    def runner(*_args, **_kwargs):
        nonlocal called
        called = True
    request = ScriptLaunchRequest(
        execution_class=ExecutionClass.SANDBOX, trust_source=TrustSource.WORKFLOW, script=link,
        boundary=EffectiveBoundary(cwd=tmp_path, readable_roots=(tmp_path,), writable_roots=(tmp_path,), environment={"PATH": "/usr/bin"}),
    )
    result = SandboxExecutor(codex_path="codex", runner=runner).run(request)
    assert result.receipt.outcome == "denied"
    assert called is False
