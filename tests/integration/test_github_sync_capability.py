from pathlib import Path

from cafe.core import capabilities as cap


def test_confirmed_artifact_sync_uses_registered_adapter_not_skill_override(monkeypatch, tmp_path: Path) -> None:
    output = tmp_path / ".cafe" / "issues" / "issue1" / "plan" / "iteration_001" / "output.md"
    output.parent.mkdir(parents=True)
    output.write_text("confirmed plan", encoding="utf-8")
    (output.parents[2] / "issue.yaml").write_text("spec:\n  issue_id: 42\nplan:\n  sync_github: true\n", encoding="utf-8")
    override = tmp_path / ".codex" / "skills" / "cafe-plan" / "scripts" / "sync_github.sh"
    override.parent.mkdir(parents=True)
    override.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    comments = []
    monkeypatch.setattr(cap.GitHubOps, "add_issue_comment", lambda self, issue, body: comments.append((issue, body)))
    registry = cap.load_capability_registry(cap.default_capability_definition_dirs(tmp_path))
    manifest = registry[cap.CAPABILITY_ISSUE_COMMENT_ID]
    request = {
        "capability": manifest.id,
        "args": {"phase": "plan", "output": str(output.relative_to(tmp_path))},
        "effects": manifest.effects.model_dump(mode="json"),
        "credentials": list(manifest.credentials),
        "permissions": {key: list(values) for key, values in manifest.permissions.items()},
    }
    run = cap.run_capability_request(repo_root=tmp_path, registry=registry, capability_request=request, output_file=output)
    assert run.receipt["success"] is True
    assert run.receipt["execution_class"] == "capability"
    assert comments and comments[0][0] == "42"
