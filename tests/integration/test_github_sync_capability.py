from pathlib import Path
from types import SimpleNamespace

import pytest

from cafe.core import capabilities as cap
from cafe.core.blackboard import BlackboardStore
from cafe.phases.generic_phase import GenericPhase
from cafe.skills.loader import SkillLoader


def test_confirmed_artifact_sync_uses_registered_adapter_not_skill_override(
    monkeypatch, tmp_path: Path
) -> None:
    output = tmp_path / ".cafe" / "issues" / "issue1" / "plan" / "iteration_001" / "output.md"
    output.parent.mkdir(parents=True)
    output.write_text("confirmed plan", encoding="utf-8")
    (output.parents[2] / "issue.yaml").write_text(
        "spec:\n  issue_id: 42\nplan:\n  sync_github: true\n", encoding="utf-8"
    )
    override = tmp_path / ".codex" / "skills" / "cafe-plan" / "scripts" / "sync_github.sh"
    override.parent.mkdir(parents=True)
    override.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    comments = []
    monkeypatch.setattr(
        cap.GitHubOps, "add_issue_comment", lambda self, issue, body: comments.append((issue, body))
    )
    registry = cap.load_capability_registry(cap.default_capability_definition_dirs(tmp_path))
    manifest = registry[cap.CAPABILITY_ISSUE_COMMENT_ID]
    digest = cap.hashlib.sha256(output.read_bytes()).hexdigest()
    issue_write = "github_issue_comment:42"
    request = {
        "capability": manifest.id,
        "args": {
            "phase": "plan",
            "output": str(output.relative_to(tmp_path)),
            "issue_id": "42",
            "artifact_sha256": digest,
        },
        "effects": {
            "writes": [issue_write],
            "network_destinations": list(manifest.effects.network_destinations),
            "browser_open": [],
        },
        "credentials": list(manifest.credentials),
        "permissions": {
            "network": list(manifest.permissions["network"]),
            "writes": [issue_write],
        },
    }
    run = cap.run_capability_request(
        repo_root=tmp_path, registry=registry, capability_request=request, output_file=output
    )
    assert run.receipt["success"] is True
    assert run.receipt["execution_class"] == "capability"
    assert run.receipt["inputs"]["issue_id"] == "42"
    assert run.receipt["inputs"]["artifact_sha256"] == digest
    assert run.receipt["requested_effects"]["writes"] == [issue_write]
    assert comments and comments[0][0] == "42"


@pytest.mark.parametrize("phase_name", ["spec", "plan"])
def test_runtime_injects_confirmed_sync_and_persists_exact_receipt(
    monkeypatch, tmp_path: Path, phase_name: str
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "issue1"
    output = issue_dir / phase_name / "iteration_001" / "output.md"
    output.parent.mkdir(parents=True)
    output.write_text(f"confirmed {phase_name}", encoding="utf-8")
    (issue_dir / "issue.yaml").write_text(
        f"spec:\n  issue_id: 42\n  sync_github: {'true' if phase_name == 'spec' else 'false'}\n"
        f"plan:\n  sync_github: {'true' if phase_name == 'plan' else 'false'}\n",
        encoding="utf-8",
    )
    skill_name = f"cafe-{phase_name}"
    skill_root = tmp_path / "builtin" / "skills" / skill_name
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        f"---\nname: {skill_name}\ndescription: test\n---\n", encoding="utf-8"
    )
    loader = SkillLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=tmp_path / "builtin",
    )
    loader.discover()
    state = BlackboardStore(issue_dir).load_or_create(phase_name)
    captured = {}

    def run_request(**kwargs):
        captured.update(kwargs["capability_request"])
        return SimpleNamespace(
            receipt={
                "capability": cap.CAPABILITY_ISSUE_COMMENT_ID,
                "correlation_id": "sync-correlation",
                "success": True,
                "inputs": dict(kwargs["capability_request"]["args"]),
            }
        )

    monkeypatch.setattr("cafe.phases.generic_phase.run_capability_request", run_request)
    result = GenericPhase(loader).execute(
        skill_name=skill_name,
        skill_invocation=f"/{phase_name}",
        step_def={
            "output_artifact": phase_name,
            "hooks": {},
            "valid_intents": ["confirmed", "need_permission"],
        },
        agent_executor=lambda _prompt: "confirmed",
        output_file=output,
        hook_context={
            "phase": SimpleNamespace(issue_dir=issue_dir),
            "step_name": phase_name,
            "output_file": output,
            "blackboard_state": state,
        },
    )

    assert any(event.get("type") == "capability_hook" for event in result.events)
    assert captured["capability"] == cap.CAPABILITY_ISSUE_COMMENT_ID
    assert captured["args"]["phase"] == phase_name
    assert captured["args"]["issue_id"] == "42"
    assert (
        captured["args"]["artifact_sha256"] == cap.hashlib.sha256(output.read_bytes()).hexdigest()
    )
    receipts = BlackboardStore(issue_dir).load_or_create(phase_name).capability_receipts
    assert receipts[-1]["correlation_id"] == "sync-correlation"


@pytest.mark.parametrize("override_source", ["project", "global"])
def test_runtime_does_not_inject_confirmed_sync_for_skill_override(
    monkeypatch, tmp_path: Path, override_source: str
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "issue1"
    output = issue_dir / "plan" / "iteration_001" / "output.md"
    output.parent.mkdir(parents=True)
    output.write_text("untrusted plan override", encoding="utf-8")
    (issue_dir / "issue.yaml").write_text(
        "spec:\n  issue_id: 42\nplan:\n  sync_github: true\n", encoding="utf-8"
    )
    builtin = tmp_path / "builtin" / "skills" / "cafe-plan"
    builtin.mkdir(parents=True)
    (builtin / "SKILL.md").write_text(
        "---\nname: cafe-plan\ndescription: builtin\n---\n", encoding="utf-8"
    )
    override_root = (
        tmp_path / "project" / ".cafe" / "skills"
        if override_source == "project"
        else tmp_path / "global" / "skills"
    )
    override = override_root / "cafe-plan"
    override.mkdir(parents=True)
    (override / "SKILL.md").write_text(
        "---\nname: cafe-plan\ndescription: override\n---\n", encoding="utf-8"
    )
    loader = SkillLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=tmp_path / "builtin",
    )
    loader.discover()
    capability_calls = []
    monkeypatch.setattr(
        "cafe.phases.generic_phase.run_capability_request",
        lambda **kwargs: capability_calls.append(kwargs),
    )

    result = GenericPhase(loader).execute(
        skill_name="cafe-plan",
        skill_invocation="/plan",
        step_def={
            "output_artifact": "plan",
            "hooks": {},
            "valid_intents": ["confirmed", "need_permission"],
        },
        agent_executor=lambda _prompt: "confirmed",
        output_file=output,
        hook_context={
            "phase": SimpleNamespace(issue_dir=issue_dir),
            "step_name": "plan",
            "output_file": output,
            "blackboard_state": BlackboardStore(issue_dir).load_or_create("plan"),
        },
    )

    assert capability_calls == []
    assert not any(event.get("type") == "capability_hook" for event in result.events)


def test_runtime_does_not_inject_confirmed_sync_for_mismatched_artifact(
    monkeypatch, tmp_path: Path
) -> None:
    issue_dir = tmp_path / ".cafe" / "issues" / "issue1"
    output = issue_dir / "custom" / "iteration_001" / "output.md"
    output.parent.mkdir(parents=True)
    output.write_text("custom artifact", encoding="utf-8")
    (issue_dir / "issue.yaml").write_text(
        "spec:\n  issue_id: 42\nplan:\n  sync_github: true\n", encoding="utf-8"
    )
    builtin = tmp_path / "builtin" / "skills" / "cafe-plan"
    builtin.mkdir(parents=True)
    (builtin / "SKILL.md").write_text(
        "---\nname: cafe-plan\ndescription: builtin\n---\n", encoding="utf-8"
    )
    loader = SkillLoader(
        project_root=tmp_path / "project",
        global_root=tmp_path / "global",
        builtin_root=tmp_path / "builtin",
    )
    loader.discover()
    capability_calls = []
    monkeypatch.setattr(
        "cafe.phases.generic_phase.run_capability_request",
        lambda **kwargs: capability_calls.append(kwargs),
    )

    result = GenericPhase(loader).execute(
        skill_name="cafe-plan",
        skill_invocation="/custom",
        step_def={
            "output_artifact": "custom",
            "hooks": {},
            "valid_intents": ["confirmed", "need_permission"],
        },
        agent_executor=lambda _prompt: "confirmed",
        output_file=output,
        hook_context={
            "phase": SimpleNamespace(issue_dir=issue_dir),
            "step_name": "custom",
            "output_file": output,
            "blackboard_state": BlackboardStore(issue_dir).load_or_create("custom"),
        },
    )

    assert capability_calls == []
    assert not any(event.get("type") == "capability_hook" for event in result.events)
