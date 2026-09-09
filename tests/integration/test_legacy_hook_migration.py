from pathlib import Path

from cafe.core.sandbox_execution import MIGRATION_GUIDANCE, SandboxExecutor
from cafe.core.execution_boundary import EffectiveBoundary, ExecutionClass, ScriptLaunchRequest, TrustSource


def test_legacy_hook_without_sandbox_backend_is_denied_with_migration_choices(tmp_path: Path) -> None:
    script = tmp_path / "legacy.sh"
    script.write_text("#!/bin/sh\necho $GH_TOKEN\n", encoding="utf-8")
    request = ScriptLaunchRequest(
        execution_class=ExecutionClass.SANDBOX, trust_source=TrustSource.WORKFLOW,
        script=script,
        boundary=EffectiveBoundary(cwd=tmp_path, readable_roots=(tmp_path,), writable_roots=(tmp_path,), environment={"GH_TOKEN": "sentinel", "PATH": "/usr/bin"}),
    )
    result = SandboxExecutor(codex_path=None).run(request)
    receipt = result.receipt.model_dump(mode="json")
    assert receipt["outcome"] == "denied"
    assert MIGRATION_GUIDANCE in receipt["details"]["migration"]
    assert "sentinel" not in str(receipt)
