"""I1/I4: prepare links resulting checkouts and refreshes legacy upgrade copies."""

from tests.prepare_support import with_delivery_choices

import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from cafe.core.types import AgentCLI
from cafe.skills.native_bridge import NativeSkillBridge
from cafe.ui.cli import app
from tests.conftest import create_minimal_config

pytestmark = pytest.mark.usefixtures("cached_builtin_playbook_models", "isolated_global_catalog")


@pytest.fixture(autouse=True)
def offline_prepare(monkeypatch):
    monkeypatch.setattr("cafe.ui.commands.lifecycle._offer_cli_update", lambda **kwargs: None)


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout


def prepare_args(name):
    return [
        "prepare",
        name,
        "--base",
        "main",
        "--no-interactive",
        "--input-method=manual",
        "--rigor=medium",
        "--spec-template=auto",
        "--plan-template=default",
        "--no-auto-create-pr",
    ]


def repo_with_local_skill(root):
    root.mkdir()
    create_minimal_config(root)
    directory = root / ".cafe/skills/local-only"
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text("---\nname: local-only\ndescription: local\n---\nsource\n")
    (directory / "references").mkdir()
    (directory / "references/note.md").write_text("nested")
    (root / ".gitignore").write_text(".cafe/\n.codex/\n.claude/\n.copilot/\n")
    git(root, "init", "-b", "main")
    git(root, "add", ".")
    git(root, "add", "-f", ".cafe/skills")
    git(root, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "base")
    return directory


@pytest.mark.parametrize("worktree", [False, True])
def test_prepare_exposes_resulting_root_and_reruns_without_source_changes(
    tmp_path, monkeypatch, worktree
):
    repo = tmp_path / "repo"
    source = repo_with_local_skill(repo)
    before = (source / "SKILL.md").read_bytes()
    monkeypatch.chdir(repo)
    arguments = prepare_args("local-task")
    active = tmp_path / "active" if worktree else repo
    if worktree:
        arguments += ["--worktree", str(active)]
    for _ in range(2):
        result = CliRunner().invoke(app, with_delivery_choices(arguments))
        assert result.exit_code == 0, result.output
        for cli in NativeSkillBridge.PROJECT_LINK_CLIS:
            root = active / NativeSkillBridge.CLI_SKILL_DIRS[cli]
            link = root / "local-only"
            assert root.is_dir() and not root.is_symlink()
            assert link.is_symlink() and link.resolve() == active / ".cafe/skills/local-only"
            assert not Path(os.readlink(link)).is_absolute()
            assert (link / "references/note.md").read_text() == "nested"
    assert (source / "SKILL.md").read_bytes() == before
    if worktree:
        assert not (repo / ".codex/skills").exists()


def test_prepare_git_failure_installs_no_project_links(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo_with_local_skill(repo)
    monkeypatch.chdir(repo)
    from cafe.core.git import GitError, GitOperations

    with patch.object(
        GitOperations, "create_branch", side_effect=GitError("injected checkout failure")
    ):
        result = CliRunner().invoke(app, with_delivery_choices(prepare_args("failed-task")))
    assert result.exit_code != 0
    assert all(
        not (repo / NativeSkillBridge.CLI_SKILL_DIRS[cli]).exists()
        for cli in NativeSkillBridge.PROJECT_LINK_CLIS
    )


def test_prepare_upgrade_refreshes_legacy_copy_and_repairs_broken_copilot_root(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"
    source = repo_with_local_skill(repo)
    target = repo / ".codex/skills/local-only"
    shutil.copytree(source, target)
    (target.parent / NativeSkillBridge.MANAGED_SKILLS_MANIFEST).write_text('["local-only"]')
    broken = repo / ".copilot/skills"
    broken.parent.mkdir()
    broken.symlink_to(".claude/skills")
    monkeypatch.chdir(repo)
    for _ in range(2):
        result = CliRunner().invoke(app, with_delivery_choices(prepare_args("upgrade-task")))
        assert result.exit_code == 0, result.output
        assert "incomplete" not in result.output
        assert target.is_symlink() and target.resolve() == source
        assert (target / "SKILL.md").read_bytes() == (source / "SKILL.md").read_bytes()
        assert not broken.is_symlink() and (broken / "local-only").resolve() == source
        assert (repo / ".claude/skills/local-only").resolve() == source


def test_two_worktree_contexts_render_current_values_without_source_mutation(tmp_path, monkeypatch):
    """I3/U6: real loader/bridge/prompt construction isolates worktree contexts."""
    from cafe.phases.generic_phase import GenericPhase
    from cafe.skills.loader import SkillLoader

    repo = tmp_path / "repo"
    source = repo_with_local_skill(repo)
    skill_file = source / "SKILL.md"
    skill_file.write_text("""---
name: local-only
description: local
workflow:
  prompt_inputs:
    - artifacts: [notes]
      placeholder: notes_file
      required: false
  prompt_references:
    notes_instruction: note.md
---
Current {issue_dir} {iteration_dir}.
{notes_instruction}
""")
    (source / "references/note.md").write_text("Optional notes {notes_file}")
    git(repo, "add", "-f", ".cafe/skills")
    git(
        repo, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "context"
    )
    active = tmp_path / "active"
    git(repo, "worktree", "add", "-b", "parallel", str(active))
    monkeypatch.chdir(repo)
    prompts = []
    for root, issue, notes in ((repo, "alpha", "alpha.md"), (active, "beta", None)):
        loader = SkillLoader(project_root=root, global_root=tmp_path / "global")
        bridge = NativeSkillBridge(loader, project_root=root, home_dir=tmp_path / "home")
        assert bridge.reconcile_project_skills() == []
        phase = GenericPhase(loader, skill_bridge=bridge)
        current_source = root / ".cafe/skills/local-only"
        before = {p: p.read_bytes() for p in current_source.rglob("*") if p.is_file()}
        context = {
            "issue_dir": f"{root}/.cafe/issues/{issue}",
            "iteration_dir": f"{root}/.cafe/issues/{issue}/custom/iteration_007",
        }
        if notes:
            context["notes_file"] = notes
        invocation = phase.prepare_skill(
            skill_name="local-only", agent_cli=AgentCLI.CODEX, context=context
        )
        prompt = phase.build_prompt(
            skill_name="local-only", skill_invocation=invocation, context=context
        )
        assert f"Current {context['issue_dir']} {context['iteration_dir']}." in prompt
        assert "{notes_instruction}" not in prompt
        assert {p: p.read_bytes() for p in before} == before
        assert (root / ".codex/skills/local-only").resolve() == current_source
        prompts.append(prompt)
    assert "alpha.md" in prompts[0] and "alpha.md" not in prompts[1]
    assert str(repo / ".cafe/issues/alpha") not in prompts[1]
