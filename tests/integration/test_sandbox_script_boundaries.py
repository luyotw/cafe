import os
import shutil
import socket
import subprocess
from pathlib import Path

from cafe.core.execution_boundary import (
    EffectiveBoundary,
    ExecutionClass,
    ScriptLaunchRequest,
    TrustSource,
)
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
        execution_class=ExecutionClass.SANDBOX,
        trust_source=TrustSource.WORKFLOW,
        script=script,
        boundary=EffectiveBoundary(
            cwd=tmp_path,
            readable_roots=(tmp_path,),
            writable_roots=(tmp_path,),
            environment={
                "PATH": os.environ.get("PATH", ""),
                "GH_TOKEN": "sentinel",
                "HTTPS_PROXY": "sentinel",
            },
        ),
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
        execution_class=ExecutionClass.SANDBOX,
        trust_source=TrustSource.WORKFLOW,
        script=link,
        boundary=EffectiveBoundary(
            cwd=tmp_path,
            readable_roots=(tmp_path,),
            writable_roots=(tmp_path,),
            environment={"PATH": "/usr/bin"},
        ),
    )
    result = SandboxExecutor(codex_path="codex", runner=runner).run(request)
    assert result.receipt.outcome == "denied"
    assert called is False


def test_real_sandbox_enforces_environment_network_and_write_roots(tmp_path: Path) -> None:
    codex = shutil.which("codex")
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside.txt"
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    script = allowed / "probe.py"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import os, socket, sys\n"
        "from pathlib import Path\n"
        "inside, outside, port = Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3])\n"
        "inside.write_text('ok')\n"
        "try:\n outside.write_text('escaped')\n except OSError:\n pass\n"
        "try:\n"
        " socket.create_connection(('127.0.0.1', port), timeout=.2)\n"
        "except OSError:\n pass\n"
        "else:\n raise SystemExit(9)\n"
        "print('secret=' + str('GH_TOKEN' in os.environ))\n",
        encoding="utf-8",
    )
    script.chmod(0o700)
    request = ScriptLaunchRequest(
        execution_class=ExecutionClass.SANDBOX,
        trust_source=TrustSource.WORKFLOW,
        script=script,
        args=(str(allowed / "inside.txt"), str(outside), str(port)),
        boundary=EffectiveBoundary(
            cwd=allowed,
            readable_roots=(allowed,),
            writable_roots=(allowed,),
            environment={"PATH": os.environ.get("PATH", ""), "GH_TOKEN": "sentinel"},
        ),
    )
    try:
        result = SandboxExecutor(codex_path=codex).run(request)
    finally:
        listener.close()

    if result.receipt.outcome == "success":
        assert (allowed / "inside.txt").read_text(encoding="utf-8") == "ok"
        assert not outside.exists()
        assert "secret=False" in result.stdout
    elif codex is None:
        assert result.receipt.outcome == "denied"
        assert result.receipt.details["reason"] == "sandbox_backend_unavailable"
        assert not (allowed / "inside.txt").exists()
        assert not outside.exists()
    else:
        assert result.receipt.outcome == "failed"
        assert "bwrap: loopback:" in result.stderr
        assert "Operation not permitted" in result.stderr
        assert not (allowed / "inside.txt").exists()
        assert not outside.exists()
