"""Semantic phase-config binding tests."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cafe.catalogs.resolver import CatalogResolver
from cafe.ui import cli_shared
from cafe.utils.config import ConfigManager
from cafe.workflow_execution.phase_bindings import (
    resolve_phase_binding,
    resolve_phase_bindings,
)


WRITER_SCRIPT = (
    Path(__file__).parents[2]
    / "src/cafe/data/skills/use-cafe-workflow/scripts/write_phase_config.py"
)


def _playbook(
    *,
    role: str = "developer",
    default_agent: str = "David",
    assignee_type: str = "agent",
) -> dict[str, object]:
    return {
        "playbook": {"id": "binding-test"},
        "roles": {role: {"default_agent": default_agent}},
        "steps": {
            "develop": {
                "role": role,
                "assignee_type": assignee_type,
            }
        },
    }


def _write_agent(project_root: Path, role: str, name: str) -> Path:
    path = project_root / ".cafe" / "agents" / role / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: test agent\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    return path


def _write_phase_config(project_root: Path, document: dict[str, object]) -> Path:
    path = project_root / ".cafe" / "phases.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for step_name, raw_step in document.items():
        assert isinstance(raw_step, dict)
        lines.append(f"{step_name}:")
        for field in ("name", "role"):
            value = raw_step.get(field)
            if value is not None:
                lines.append(f"  {field}: {value}")
        lines.append("  clis:")
        for cli in raw_step["clis"]:
            assert isinstance(cli, dict)
            lines.extend((f"    - cli: {cli['cli']}", f"      model: {cli['model']}"))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_issue_config(project_root: Path, *, playbook_id: str = "binding-test") -> Path:
    path = project_root / ".cafe" / "issues" / "issue-binding" / "issue.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"playbook_id: {playbook_id}\n", encoding="utf-8")
    return path


def _resolver(project_root: Path, *, canonical_root: Path | None = None) -> CatalogResolver:
    return CatalogResolver(
        project_root=project_root,
        canonical_root=canonical_root or project_root,
        global_root=project_root.parent / "global",
        builtin_root=project_root.parent / "builtin",
        git_runner=lambda _args, _cwd: (_ for _ in ()).throw(ValueError("not a git test")),
    )


def _writer_module():
    spec = importlib.util.spec_from_file_location("phase_binding_writer_test", WRITER_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_rejects_display_role_name_that_is_not_an_agent_catalog_key(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _write_agent(project, "developer", "David")
    phase_path = _write_phase_config(
        project,
        {
            "develop": {
                "name": "Developer",
                "role": "developer",
                "clis": [{"cli": "codex", "model": "gpt-5.6-sol"}],
            }
        },
    )

    with pytest.raises(ValueError, match="configured agent 'Developer' for role 'developer'"):
        resolve_phase_binding(
            playbook=_playbook(),
            step_name="develop",
            local_path=phase_path,
            catalog_resolver=_resolver(project),
        )


def test_accepts_catalogued_non_default_agent_override(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _write_agent(project, "developer", "David")
    override = _write_agent(project, "developer", "PhaseDavid")
    phase_path = _write_phase_config(
        project,
        {
            "develop": {
                "name": "PhaseDavid",
                "role": "developer",
                "clis": [{"cli": "codex", "model": "gpt-5.6-sol"}],
            }
        },
    )

    binding = resolve_phase_binding(
        playbook=_playbook(),
        step_name="develop",
        local_path=phase_path,
        catalog_resolver=_resolver(project),
    )

    assert binding.agent_name == "PhaseDavid"
    assert binding.agent.path == override


def test_rejects_role_mismatch_before_agent_manager_setup(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    _write_agent(project, "developer", "David")
    phase_path = _write_phase_config(
        project,
        {
            "develop": {
                "name": "David",
                "role": "reviewer",
                "clis": [{"cli": "codex", "model": "gpt-5.6-sol"}],
            }
        },
    )
    assert phase_path.exists()
    manager_factory = MagicMock()
    monkeypatch.setattr(cli_shared, "AgentManager", manager_factory)
    monkeypatch.setattr(cli_shared, "get_git_toplevel", lambda: project)
    monkeypatch.setattr(cli_shared, "get_repo_root", lambda: project)

    with pytest.raises(ValueError, match="phase binding role mismatch"):
        cli_shared.setup_agents(
            ConfigManager(str(project / ".cafe" / "config.yaml")),
            issue_name="issue-binding",
            phase_name="develop",
            playbook_data=_playbook(),
        )

    manager_factory.assert_not_called()


def test_active_worktree_invalid_agent_shadow_fails_closed(tmp_path: Path) -> None:
    canonical = tmp_path / "canonical"
    worktree = tmp_path / "worktree"
    _write_agent(canonical, "developer", "David")
    invalid = worktree / ".cafe" / "agents" / "developer" / "David.md"
    invalid.parent.mkdir(parents=True)
    invalid.write_text("not an agent definition\n", encoding="utf-8")
    phase_path = _write_phase_config(
        worktree,
        {
            "develop": {
                "name": "David",
                "role": "developer",
                "clis": [{"cli": "codex", "model": "gpt-5.6-sol"}],
            }
        },
    )

    with pytest.raises(ValueError, match="Invalid agent developer/David"):
        resolve_phase_binding(
            playbook=_playbook(),
            step_name="develop",
            local_path=phase_path,
            catalog_resolver=_resolver(worktree, canonical_root=canonical),
        )


def test_active_worktree_agent_and_phase_fields_take_precedence(tmp_path: Path) -> None:
    canonical = tmp_path / "canonical"
    worktree = tmp_path / "worktree"
    _write_agent(canonical, "developer", "David")
    active_agent = _write_agent(worktree, "developer", "David")
    repo_phase_path = _write_phase_config(
        canonical,
        {
            "develop": {
                "name": "David",
                "role": "developer",
                "clis": [{"cli": "claude", "model": "repo-model"}],
            }
        },
    )
    local_phase_path = _write_phase_config(
        worktree,
        {
            "develop": {
                "clis": [{"cli": "codex", "model": "worktree-model"}],
            }
        },
    )

    binding = resolve_phase_binding(
        playbook=_playbook(),
        step_name="develop",
        local_path=local_phase_path,
        repo_path=repo_phase_path,
        catalog_resolver=_resolver(worktree, canonical_root=canonical),
    )

    assert binding.agent.path == active_agent
    assert binding.phase.clis == (("codex", "worktree-model"),)


def test_resolve_all_bindings_skips_human_and_automatic_steps(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _write_agent(project, "developer", "David")
    phase_path = _write_phase_config(
        project,
        {
            "develop": {
                "name": "David",
                "clis": [{"cli": "codex", "model": "gpt-5.6-sol"}],
            }
        },
    )
    playbook = _playbook()
    playbook["steps"] = {
        "develop": {"role": "developer", "assignee_type": "agent"},
        "approve": {"role": "developer", "assignee_type": "human"},
        "archive": {"role": "developer", "assignee_type": "auto"},
    }

    bindings = resolve_phase_bindings(
        playbook=playbook,
        local_path=phase_path,
        catalog_resolver=_resolver(project),
    )

    assert list(bindings) == ["develop"]


def test_writer_derives_default_agent_and_preserves_target_on_semantic_failure(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    _write_agent(project, "developer", "David")
    target = project / ".cafe" / "phases.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    chains = project / "chains.json"
    writer = _writer_module()
    resolver = _resolver(project)

    chains.write_text(
        json.dumps(
            {
                "develop": {
                    "role": "developer",
                    "clis": [{"cli": "codex", "model": "gpt-5.6-sol"}],
                }
            }
        ),
        encoding="utf-8",
    )
    writer.write_phase_config(
        chains_file=chains,
        target=target,
        playbook=_playbook(),
        project_root=project,
        catalog_resolver=resolver,
    )
    assert "name: David" in target.read_text(encoding="utf-8")

    original = target.read_text(encoding="utf-8")
    chains.write_text(
        json.dumps(
            {
                "develop": {
                    "name": "Developer",
                    "role": "developer",
                    "clis": [{"cli": "codex", "model": "gpt-5.6-sol"}],
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="configured agent 'Developer' for role 'developer'"):
        writer.write_phase_config(
            chains_file=chains,
            target=target,
            playbook=_playbook(),
            project_root=project,
            catalog_resolver=resolver,
        )

    assert target.read_text(encoding="utf-8") == original


def test_legacy_writer_cli_derives_default_from_the_target_issue_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    _write_agent(project, "developer", "David")
    _write_issue_config(project)
    target = project / ".cafe" / "phases.yaml"
    chains = project / "chains.json"
    chains.write_text(
        json.dumps(
            {
                "develop": {
                    "role": "developer",
                    "clis": [{"cli": "codex", "model": "gpt-5.6-sol"}],
                }
            }
        ),
        encoding="utf-8",
    )
    writer = _writer_module()
    monkeypatch.setattr(writer, "_load_effective_playbook", lambda **_kwargs: _playbook())
    monkeypatch.setattr(
        sys,
        "argv",
        [str(WRITER_SCRIPT), "--chains-json", str(chains), "--target", str(target)],
    )

    assert writer.main() == 0
    assert "name: David" in target.read_text(encoding="utf-8")

    original = target.read_text(encoding="utf-8")
    chains.write_text(
        json.dumps(
            {
                "develop": {
                    "name": "__missing_phase_binding_agent__",
                    "role": "developer",
                    "clis": [{"cli": "codex", "model": "gpt-5.6-sol"}],
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as exc_info:
        writer.main()

    assert exc_info.value.code == 2
    assert target.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("git_marker", ("directory", "worktree-file"))
def test_legacy_writer_cli_rejects_zero_issue_workflow_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, git_marker: str
) -> None:
    project = tmp_path / "project"
    marker = project / ".git"
    marker.parent.mkdir(parents=True)
    if git_marker == "directory":
        marker.mkdir()
    else:
        marker.write_text("gitdir: /canonical/.git/worktrees/issue-binding\n", encoding="utf-8")
    target = project / ".cafe" / "phases.yaml"
    target.parent.mkdir(parents=True)
    original = "develop:\n  name: old\n"
    target.write_text(original, encoding="utf-8")
    chains = project / "chains.json"
    chains.write_text(
        json.dumps(
            {
                "develop": {
                    "name": "__missing_phase_binding_agent__",
                    "role": "developer",
                    "clis": [{"cli": "codex", "model": "gpt-5.6-sol"}],
                }
            }
        ),
        encoding="utf-8",
    )
    writer = _writer_module()
    monkeypatch.setattr(
        sys,
        "argv",
        [str(WRITER_SCRIPT), "--chains-json", str(chains), "--target", str(target)],
    )

    with pytest.raises(SystemExit) as exc_info:
        writer.main()

    assert exc_info.value.code == 2
    assert target.read_text(encoding="utf-8") == original


def test_legacy_writer_cli_rejects_empty_issue_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    (project / ".cafe" / "issues").mkdir(parents=True)
    target = project / ".cafe" / "phases.yaml"
    original = "develop:\n  name: old\n"
    target.write_text(original, encoding="utf-8")
    chains = project / "chains.json"
    chains.write_text(
        json.dumps(
            {
                "develop": {
                    "name": "__missing_phase_binding_agent__",
                    "role": "developer",
                    "clis": [{"cli": "codex", "model": "gpt-5.6-sol"}],
                }
            }
        ),
        encoding="utf-8",
    )
    writer = _writer_module()
    monkeypatch.setattr(
        sys,
        "argv",
        [str(WRITER_SCRIPT), "--chains-json", str(chains), "--target", str(target)],
    )

    with pytest.raises(SystemExit) as exc_info:
        writer.main()

    assert exc_info.value.code == 2
    assert target.read_text(encoding="utf-8") == original


def test_writer_keeps_standalone_standard_target_compatible(tmp_path: Path) -> None:
    project = tmp_path / "standalone"
    target = project / ".cafe" / "phases.yaml"
    chains = project / "chains.json"
    chains.parent.mkdir(parents=True)
    chains.write_text(
        json.dumps(
            {
                "develop": {
                    "name": "David",
                    "role": "developer",
                    "clis": [{"cli": "codex", "model": "gpt-5.6-sol"}],
                }
            }
        ),
        encoding="utf-8",
    )

    _writer_module().write_phase_config(chains_file=chains, target=target)

    assert "name: David" in target.read_text(encoding="utf-8")


def test_legacy_writer_cli_rejects_multiple_issue_configs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    _write_issue_config(project)
    second_issue = project / ".cafe" / "issues" / "issue-other" / "issue.yaml"
    second_issue.parent.mkdir(parents=True)
    second_issue.write_text("playbook_id: binding-test\n", encoding="utf-8")
    target = project / ".cafe" / "phases.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    original = "develop:\n  name: old\n"
    target.write_text(original, encoding="utf-8")
    chains = project / "chains.json"
    chains.write_text(
        json.dumps(
            {
                "develop": {
                    "name": "__missing_phase_binding_agent__",
                    "role": "developer",
                    "clis": [{"cli": "codex", "model": "gpt-5.6-sol"}],
                }
            }
        ),
        encoding="utf-8",
    )
    writer = _writer_module()
    monkeypatch.setattr(
        sys,
        "argv",
        [str(WRITER_SCRIPT), "--chains-json", str(chains), "--target", str(target)],
    )

    with pytest.raises(SystemExit) as exc_info:
        writer.main()

    assert exc_info.value.code == 2
    assert target.read_text(encoding="utf-8") == original


def test_writer_validates_all_effective_agent_steps_before_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    _write_agent(project, "developer", "David")
    _write_issue_config(project)
    target = project / ".cafe" / "phases.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    original = "develop:\n  name: old\n"
    target.write_text(original, encoding="utf-8")
    chains = project / "chains.json"
    chains.write_text(
        json.dumps(
            {
                "develop": {
                    "name": "David",
                    "role": "developer",
                    "clis": [{"cli": "codex", "model": "gpt-5.6-sol"}],
                }
            }
        ),
        encoding="utf-8",
    )
    playbook = _playbook()
    playbook["steps"] = {
        "develop": {"role": "developer", "assignee_type": "agent"},
        "pr": {"role": "developer", "assignee_type": "hybrid"},
    }
    writer = _writer_module()
    monkeypatch.setattr(writer, "_load_effective_playbook", lambda **_kwargs: playbook)

    with pytest.raises(ValueError, match="step='pr'"):
        writer.write_phase_config(
            chains_file=chains,
            target=target,
            catalog_resolver=_resolver(project),
        )

    assert target.read_text(encoding="utf-8") == original


def test_writer_rejects_explicit_playbook_that_differs_from_issue_config(tmp_path: Path) -> None:
    project = tmp_path / "project"
    issue_config = _write_issue_config(project, playbook_id="effective-playbook")
    writer = _writer_module()

    with pytest.raises(ValueError, match="differs from issue config"):
        writer._load_effective_playbook(
            project_root=project,
            playbook_id="different-playbook",
            issue_config=issue_config,
        )


def test_writer_keeps_legacy_custom_target_support(tmp_path: Path) -> None:
    chains = tmp_path / "chains.json"
    target = tmp_path / "custom-phase-config.yaml"
    chains.write_text(
        json.dumps(
            {
                "develop": {
                    "name": "David",
                    "clis": [{"cli": "codex", "model": "gpt-5.6-sol"}],
                }
            }
        ),
        encoding="utf-8",
    )

    _writer_module().write_phase_config(chains_file=chains, target=target)

    assert "name: David" in target.read_text(encoding="utf-8")
