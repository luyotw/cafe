"""Contract tests for parallel CAFE workflows installing skills in isolated project roots."""

from pathlib import Path

from cafe.core.types import AgentCLI
from cafe.skills.loader import SkillLoader
from cafe.skills.native_bridge import NativeSkillBridge


def _write_plan_skill(root: Path, body: str) -> None:
    skill_dir = root / "cafe-plan"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: cafe-plan\ndescription: test plan\n---\n\n{body}\n",
        encoding="utf-8",
    )


def _bridge_for_project(
    tmp_path: Path,
    *,
    project_root: Path,
    global_root: Path,
    home_dir: Path,
) -> NativeSkillBridge:
    builtin = tmp_path / "builtin" / "skills"
    _write_plan_skill(builtin, "builtin plan")
    loader = SkillLoader(
        project_root=project_root,
        global_root=global_root,
        builtin_root=tmp_path / "builtin",
    )
    loader.discover()
    return NativeSkillBridge(loader, project_root=project_root, home_dir=home_dir)


def test_parallel_workflow_skill_installs_use_isolated_project_roots(tmp_path: Path) -> None:
    """Two project roots can install the same skill without cross-writing."""
    global_root = tmp_path / "global" / "skills"
    home_dir = tmp_path / "home"
    project_a = tmp_path / "worktree-a"
    project_b = tmp_path / "worktree-b"
    for project in (project_a, project_b):
        (project / ".cafe").mkdir(parents=True)
    _write_plan_skill(project_a / ".cafe" / "skills", "Project A plan")
    _write_plan_skill(project_b / ".cafe" / "skills", "Project B plan")

    bridge_a = _bridge_for_project(
        tmp_path, project_root=project_a, global_root=global_root, home_dir=home_dir
    )
    bridge_b = _bridge_for_project(
        tmp_path, project_root=project_b, global_root=global_root, home_dir=home_dir
    )

    bridge_a.install_skill("cafe-plan", AgentCLI.CODEX)
    bridge_b.install_skill("cafe-plan", AgentCLI.CODEX)

    installed_a = project_a / ".codex" / "skills" / "cafe-plan" / "SKILL.md"
    installed_b = project_b / ".codex" / "skills" / "cafe-plan" / "SKILL.md"
    assert "Project A plan" in installed_a.read_text(encoding="utf-8")
    assert "Project B plan" in installed_b.read_text(encoding="utf-8")

    installed_a.write_text("mutated in A\n", encoding="utf-8")
    assert "mutated in A" not in installed_b.read_text(encoding="utf-8")
    assert "Project B plan" in installed_b.read_text(encoding="utf-8")


def test_parallel_workflow_installs_do_not_write_global_home_skills(tmp_path: Path) -> None:
    """install_skill targets project-local CLI dirs, not the user home skills tree."""
    global_root = tmp_path / "global" / "skills"
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    project_root = tmp_path / "project"
    (project_root / ".cafe" / "skills").mkdir(parents=True)
    _write_plan_skill(global_root, "global plan")
    _write_plan_skill(project_root / ".cafe" / "skills", "project plan")

    global_home_skill = home_dir / ".codex" / "skills" / "cafe-plan" / "SKILL.md"
    if global_home_skill.parent.exists():
        marker = global_home_skill.read_text(encoding="utf-8")
    else:
        marker = "absent"

    bridge = _bridge_for_project(
        tmp_path, project_root=project_root, global_root=global_root, home_dir=home_dir
    )
    bridge.install_skill("cafe-plan", AgentCLI.CODEX)

    project_installed = project_root / ".codex" / "skills" / "cafe-plan" / "SKILL.md"
    assert project_installed.exists()
    assert "project plan" in project_installed.read_text(encoding="utf-8")

    global_dir = bridge.get_global_native_skills_dir(AgentCLI.CODEX)
    global_installed = global_dir / "cafe-plan"
    assert not global_installed.exists()
    if global_home_skill.exists():
        assert global_home_skill.read_text(encoding="utf-8") == marker


def test_install_skill_git_excludes_cli_dir(tmp_path: Path) -> None:
    """CAFE-managed CLI injection dir is added to .git/info/exclude so it does
    not make the worktree dirty (which would block chat-handoff consumption)."""
    import subprocess

    global_root = tmp_path / "global" / "skills"
    home_dir = tmp_path / "home"
    project = tmp_path / "repo"
    (project / ".cafe").mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=project, check=True, capture_output=True)
    _write_plan_skill(project / ".cafe" / "skills", "Project plan")

    bridge = _bridge_for_project(
        tmp_path, project_root=project, global_root=global_root, home_dir=home_dir
    )
    bridge.install_skill("cafe-plan", AgentCLI.CODEX)

    exclude = (project / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert "/.codex/" in exclude.splitlines()

    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=project, capture_output=True, text=True
    ).stdout
    assert ".codex" not in status  # the CLI dir must not show as untracked/dirty


def test_install_skill_git_exclude_is_idempotent(tmp_path: Path) -> None:
    """Re-installing does not append duplicate exclude entries."""
    import subprocess

    global_root = tmp_path / "global" / "skills"
    home_dir = tmp_path / "home"
    project = tmp_path / "repo"
    (project / ".cafe").mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=project, check=True, capture_output=True)
    _write_plan_skill(project / ".cafe" / "skills", "Project plan")

    bridge = _bridge_for_project(
        tmp_path, project_root=project, global_root=global_root, home_dir=home_dir
    )
    bridge.install_skill("cafe-plan", AgentCLI.CODEX)
    bridge.install_skill("cafe-plan", AgentCLI.CODEX)

    exclude = (project / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert exclude.splitlines().count("/.codex/") == 1


def test_install_skill_no_git_is_noop(tmp_path: Path) -> None:
    """Without a git repo, install still works and does not raise."""
    global_root = tmp_path / "global" / "skills"
    home_dir = tmp_path / "home"
    project = tmp_path / "repo"
    (project / ".cafe").mkdir(parents=True)
    _write_plan_skill(project / ".cafe" / "skills", "Project plan")

    bridge = _bridge_for_project(
        tmp_path, project_root=project, global_root=global_root, home_dir=home_dir
    )
    # Must not raise even though there is no .git
    bridge.install_skill("cafe-plan", AgentCLI.CODEX)
    assert (project / ".codex" / "skills" / "cafe-plan" / "SKILL.md").exists()
