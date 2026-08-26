"""Tests for bundled use-cafe-workflow skill guidance."""

import fcntl
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from cafe.core.playbook import confirmation_gate_steps
from cafe.core.status_codes import (
    PhaseStatusCode,
    effective_step_handoff_intents,
    effective_step_status_codes,
)
from cafe.phases.generic_phase import GenericPhase
from cafe.playbooks.loader import PlaybookLoader

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = PROJECT_ROOT / "src" / "cafe" / "data" / "skills" / "use-cafe-workflow"

DEFAULT_PHASE_CHAINS = {
    "spec": "gemini:requirements-main,copilot:requirements-fallback",
    "plan": "cursor-agent:planning-main,gemini:planning-fallback",
    "develop": "copilot:implementation-main,cursor-agent:implementation-fallback",
    "review": "gemini:review-main,copilot:review-fallback",
    "pr": "cursor-agent:publication-main,gemini:publication-fallback",
}

DEFAULT_PHASE_RATIONALES = {
    "spec": "frontier: high requirements reasoning and public-contract risk; equivalent fallback",
    "plan": "frontier: high architecture reasoning and integration risk; equivalent fallback",
    "develop": "balanced: bounded implementation with integration tests; equivalent fallback",
    "review": "frontier: high correctness and security reasoning; stronger fallback",
    "pr": "efficiency: routine publication artifact with independent host validation; equivalent fallback",
}

PRIMARY_ONLY_PHASE_CHAINS = {
    "spec": "claude:requirements-main",
    "plan": "claude:planning-main",
    "develop": "claude:implementation-main",
    "review": "claude:review-main",
    "pr": "claude:publication-main",
}


def _phase_chain_args(chains: dict[str, str] | None = None) -> list[str]:
    result: list[str] = []
    for step, chain in (chains or DEFAULT_PHASE_CHAINS).items():
        result.extend(["--phase-chain", f"{step}={chain}"])
    return result


def _phase_rationale_args(rationales: dict[str, str] | None = None) -> list[str]:
    result: list[str] = []
    for step, rationale in (rationales or DEFAULT_PHASE_RATIONALES).items():
        result.extend(["--phase-rationale", f"{step}={rationale}"])
    return result


def _read_skill_resource(path: str) -> str:
    return (SKILL_ROOT / path).read_text(encoding="utf-8")


def _kickoff_formatter_command(strategic_context: Path, *extra_args: str) -> list[str]:
    return [
        sys.executable,
        str(SKILL_ROOT / "scripts" / "format_kickoff_contract.py"),
        "standard",
        "--issue-name",
        "issue346",
        "--playbook-rationale",
        (
            "Repository policy requires the standard graph; QA is not independently "
            "required, so standard-qa is unnecessary."
        ),
        "--issue-nature",
        "feature/integration",
        "--issue-scale",
        "medium",
        "--model-adjustment-authority",
        "driver_autonomous",
        *extra_args,
        "--risk-factor",
        "public contract",
        "--assessment-rationale",
        "Changes a public workflow contract across runtime and CLI.",
        *_phase_chain_args(),
        *_phase_rationale_args(),
        "--effective-locale",
        "zh-TW",
        "--locale-source",
        "user thread override",
        "--repository-content-locale",
        "zh-TW",
        "--user-required",
        "--driver-confirmable",
        "spec",
        "plan",
        "--worktree",
        ".cafe/worktrees/issue346",
        "--strategic-context",
        str(strategic_context),
    ]


def _load_script_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_preflight_cache(
    cache_file: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SKILL_ROOT / "scripts" / "preflight_cache.py"),
            "--cache-file",
            str(cache_file),
            *args,
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )


def test_use_cafe_workflow_uses_progressive_disclosure() -> None:
    skill = _read_skill_resource("SKILL.md")
    references = (
        "playbook_selection.md",
        "kickoff.md",
        "strategic_context.md",
        "model_selection.md",
        "running_workflow.md",
        "handoffs_and_alignment.md",
        "diagnosis_and_repair.md",
        "convergent_pr_review.md",
        "correction_ab_experiment.md",
        "issue_decomposition.md",
        "project_global_skill_sync.md",
    )

    assert "## Progressive disclosure" in skill
    assert len(skill.splitlines()) <= 150
    for name in references:
        assert f"references/{name}" in skill
        assert (SKILL_ROOT / "references" / name).is_file()

    assert "## Conversation Locale" not in skill
    assert "## Driver-Owned Alignment" not in skill
    assert "## Bounded Self-Diagnosis And Declarative Repair" not in skill


def test_use_cafe_workflow_checks_project_and_global_skills_before_execution() -> None:
    skill = _read_skill_resource("SKILL.md")
    running = _read_skill_resource("references/running_workflow.md")
    reference = _read_skill_resource("references/project_global_skill_sync.md")
    normalized = " ".join(reference.split())

    assert "references/project_global_skill_sync.md" in skill
    assert "project_global_skill_sync.md" in running
    assert "<skill-dir>/scripts/project_global_skill_sync.py check" in reference
    assert "continue without mentioning the check or asking the user" in normalized
    assert "Do not update before the user explicitly agrees" in normalized
    assert "<skill-dir>/scripts/project_global_skill_sync.py update" in reference
    assert "--comparison-token <token-from-the-approved-check>" in reference
    assert "--project-root <canonical-main-worktree> update" in normalized
    assert "--project-root <canonical-main-worktree> check" in normalized
    assert "same working directory" in normalized
    assert "Re-run `check` afterward" in reference


def _write_project_sync_skill(root: Path, name: str, version: str, body: str) -> Path:
    skill = root / name
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test\nversion: {version}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return skill


def _project_global_sync_module():
    return _load_script_module(
        SKILL_ROOT / "scripts" / "project_global_skill_sync.py",
        "project_global_skill_sync",
    )


def test_project_global_skill_check_is_silent_candidate_when_identical(
    tmp_path: Path,
) -> None:
    module = _project_global_sync_module()
    project = tmp_path / "project"
    global_root = tmp_path / "global"
    project_skill = _write_project_sync_skill(
        project / ".cafe" / "skills", "cafe-example", "1.0.0", "same"
    )
    global_skill = _write_project_sync_skill(
        global_root, "cafe-example", "1.0.0", "same"
    )
    _write_project_sync_skill(global_root, "global-only", "1.0.0", "ignored")
    (project_skill / "scripts" / "__pycache__").mkdir(parents=True)
    (project_skill / "scripts" / "__pycache__" / "generated.pyc").write_bytes(b"project")
    (global_skill / "scripts" / "__pycache__").mkdir(parents=True)
    (global_skill / "scripts" / "__pycache__" / "generated.pyc").write_bytes(b"global")

    result = module.compare_skills(project_roots=(project,), global_root=global_root)

    assert result["status"] == "identical"
    assert result["compared_count"] == 1
    assert result["differences"] == []


def test_project_global_skill_check_lists_versions_and_missing_global(
    tmp_path: Path,
) -> None:
    module = _project_global_sync_module()
    project = tmp_path / "project"
    global_root = tmp_path / "global"
    _write_project_sync_skill(
        project / ".cafe" / "skills", "cafe-changed", "2.0.0", "project"
    )
    _write_project_sync_skill(global_root, "cafe-changed", "1.0.0", "global")
    _write_project_sync_skill(
        project / ".cafe" / "skills", "cafe-missing", "3.0.0", "project"
    )

    result = module.compare_skills(project_roots=(project,), global_root=global_root)
    differences = {item["skill"]: item for item in result["differences"]}

    assert result["status"] == "differences"
    assert differences["cafe-changed"]["reason"] == "content_mismatch"
    assert differences["cafe-changed"]["project_version"] == "2.0.0"
    assert differences["cafe-changed"]["global_version"] == "1.0.0"
    assert differences["cafe-missing"]["reason"] == "missing_global"
    assert differences["cafe-missing"]["global_version"] is None


def test_project_global_skill_check_binds_invalid_global_state_to_token(
    tmp_path: Path,
) -> None:
    module = _project_global_sync_module()
    project = tmp_path / "project"
    global_root = tmp_path / "global"
    _write_project_sync_skill(
        project / ".cafe" / "skills", "cafe-invalid", "2.0.0", "project"
    )
    invalid = global_root / "cafe-invalid"
    invalid.mkdir(parents=True)
    (invalid / "SKILL.md").write_text("invalid one\n", encoding="utf-8")

    first = module.compare_skills(project_roots=(project,), global_root=global_root)
    (invalid / "SKILL.md").write_text("invalid two\n", encoding="utf-8")
    second = module.compare_skills(project_roots=(project,), global_root=global_root)

    assert first["differences"][0]["reason"] == "invalid_global"
    assert first["differences"][0]["global_digest"] is not None
    assert first["comparison_token"] != second["comparison_token"]


def test_project_global_skill_check_binds_dangling_global_symlink(
    tmp_path: Path,
) -> None:
    module = _project_global_sync_module()
    project = tmp_path / "project"
    global_root = tmp_path / "global"
    _write_project_sync_skill(
        project / ".cafe" / "skills", "cafe-linked", "2.0.0", "project"
    )
    global_root.mkdir()
    link = global_root / "cafe-linked"
    link.symlink_to("missing-one", target_is_directory=True)

    first = module.compare_skills(project_roots=(project,), global_root=global_root)
    link.unlink()
    link.symlink_to("missing-two", target_is_directory=True)
    second = module.compare_skills(project_roots=(project,), global_root=global_root)

    assert first["differences"][0]["reason"] == "invalid_global"
    assert first["differences"][0]["global_digest"] is not None
    assert first["comparison_token"] != second["comparison_token"]


def test_project_global_skill_update_changes_only_approved_skills(tmp_path: Path) -> None:
    module = _project_global_sync_module()
    project = tmp_path / "project"
    global_root = tmp_path / "global"
    for name in ("cafe-approved", "cafe-unapproved"):
        _write_project_sync_skill(
            project / ".cafe" / "skills", name, "2.0.0", f"project {name}"
        )
        _write_project_sync_skill(global_root, name, "1.0.0", f"global {name}")

    checked = module.compare_skills(project_roots=(project,), global_root=global_root)
    result = module.update_skills(
        project_roots=(project,),
        global_root=global_root,
        selected=("cafe-approved",),
        comparison_token=checked["comparison_token"],
    )

    assert result["status"] == "updated"
    assert result["updated"] == ["cafe-approved"]
    remaining = {item["skill"] for item in result["comparison"]["differences"]}
    assert remaining == {"cafe-unapproved"}
    assert "project cafe-approved" in (
        global_root / "cafe-approved" / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "global cafe-unapproved" in (
        global_root / "cafe-unapproved" / "SKILL.md"
    ).read_text(encoding="utf-8")


def test_project_global_skill_discovery_reads_main_and_active_worktree_overlays(
    tmp_path: Path,
) -> None:
    module = _project_global_sync_module()
    main = tmp_path / "main"
    linked = tmp_path / "linked"
    main.mkdir()
    subprocess.run(["git", "init", "-b", "develop"], cwd=main, check=True, capture_output=True)
    (main / "seed").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "seed"], cwd=main, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "seed",
        ],
        cwd=main,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "worktree", "add", "-b", "issue", str(linked)],
        cwd=main,
        check=True,
        capture_output=True,
    )
    _write_project_sync_skill(
        main / ".cafe" / "skills", "cafe-main", "1.0.0", "main"
    )
    _write_project_sync_skill(
        linked / ".cafe" / "skills", "cafe-overlay", "1.0.0", "overlay"
    )

    roots = module.discover_project_roots(linked)
    project_skills = module._project_skills(roots)

    assert roots == (main.resolve(), linked.resolve())
    assert set(project_skills) == {"cafe-main", "cafe-overlay"}


def test_project_global_skill_discovery_supports_separate_git_directory(
    tmp_path: Path,
) -> None:
    module = _project_global_sync_module()
    main = tmp_path / "main"
    linked = tmp_path / "linked"
    git_dir = tmp_path / "separate-git-dir"
    subprocess.run(
        [
            "git",
            "init",
            "-b",
            "develop",
            "--separate-git-dir",
            str(git_dir),
            str(main),
        ],
        check=True,
        capture_output=True,
    )
    (main / "seed").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "seed"], cwd=main, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "seed",
        ],
        cwd=main,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "worktree", "add", "-b", "issue", str(linked)],
        cwd=main,
        check=True,
        capture_output=True,
    )
    _write_project_sync_skill(
        main / ".cafe" / "skills", "cafe-main", "1.0.0", "main"
    )

    roots = module.discover_project_roots(linked)
    project_skills = module._project_skills(roots)

    assert roots == (main.resolve(),)
    assert set(project_skills) == {"cafe-main"}


def test_project_global_skill_discovery_fails_closed_for_unmapped_separate_git_directory(
    tmp_path: Path, monkeypatch
) -> None:
    module = _project_global_sync_module()
    main = tmp_path / "projects" / "a" / "main"
    linked = tmp_path / "worktrees" / "x" / "linked"
    git_dir = tmp_path / "metadata" / "y" / "gitstore"
    main.parent.mkdir(parents=True)
    linked.parent.mkdir(parents=True)
    git_dir.parent.mkdir(parents=True)
    subprocess.run(
        [
            "git",
            "init",
            "-b",
            "develop",
            "--separate-git-dir",
            str(git_dir),
            str(main),
        ],
        check=True,
        capture_output=True,
    )
    (main / "seed").write_text("seed\n", encoding="utf-8")
    subprocess.run(["git", "add", "seed"], cwd=main, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "seed",
        ],
        cwd=main,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "worktree", "add", "-b", "issue", str(linked)],
        cwd=main,
        check=True,
        capture_output=True,
    )
    _write_project_sync_skill(
        main / ".cafe" / "skills", "cafe-main", "1.0.0", "main"
    )
    _write_project_sync_skill(
        linked / ".cafe" / "skills", "cafe-overlay", "1.0.0", "overlay"
    )

    with pytest.raises(module.SkillSyncError, match="--project-root"):
        module.discover_project_roots(linked)

    monkeypatch.chdir(linked)
    roots = module._resolve_project_roots(main)
    project_skills = module._project_skills(roots)

    assert roots == (main.resolve(), linked.resolve())
    assert set(project_skills) == {"cafe-main", "cafe-overlay"}


def test_project_global_skill_check_includes_symlinked_project_skills(
    tmp_path: Path,
) -> None:
    module = _project_global_sync_module()
    project = tmp_path / "project"
    external = _write_project_sync_skill(
        tmp_path / "shared", "cafe-linked", "2.0.0", "linked project skill"
    )
    project_skills = project / ".cafe" / "skills"
    project_skills.mkdir(parents=True)
    (project_skills / "cafe-linked").symlink_to(external, target_is_directory=True)

    result = module.compare_skills(
        project_roots=(project,), global_root=tmp_path / "global"
    )

    assert result["compared_count"] == 1
    assert result["differences"][0]["skill"] == "cafe-linked"
    assert result["differences"][0]["reason"] == "missing_global"


def test_project_global_skill_digest_has_unambiguous_file_boundaries(
    tmp_path: Path,
) -> None:
    module = _project_global_sync_module()
    single_file = tmp_path / "single"
    two_files = tmp_path / "two"
    single_file.mkdir()
    two_files.mkdir()
    (single_file / "a").write_bytes(b"X\0F\0b\0" + b"644" + b"\0Y")
    (two_files / "a").write_bytes(b"X")
    (two_files / "b").write_bytes(b"Y")

    assert module._tree_digest(single_file) != module._tree_digest(two_files)


@pytest.mark.parametrize("changed_side", ["project", "global"])
def test_project_global_skill_update_rejects_content_changed_after_approval(
    tmp_path: Path, changed_side: str
) -> None:
    module = _project_global_sync_module()
    project = tmp_path / "project"
    global_root = tmp_path / "global"
    project_skill = _write_project_sync_skill(
        project / ".cafe" / "skills", "cafe-drift", "2.0.0", "project"
    )
    global_skill = _write_project_sync_skill(
        global_root, "cafe-drift", "1.0.0", "global"
    )
    checked = module.compare_skills(project_roots=(project,), global_root=global_root)
    changed = project_skill if changed_side == "project" else global_skill
    (changed / "SKILL.md").write_text(
        (changed / "SKILL.md").read_text(encoding="utf-8") + "drift\n",
        encoding="utf-8",
    )

    with pytest.raises(module.SkillSyncError, match="changed after approval"):
        module.update_skills(
            project_roots=(project,),
            global_root=global_root,
            selected=("cafe-drift",),
            comparison_token=checked["comparison_token"],
        )


@pytest.mark.parametrize("changed_side", ["project", "global"])
def test_project_global_skill_update_rejects_drift_during_staging(
    tmp_path: Path, monkeypatch, changed_side: str
) -> None:
    module = _project_global_sync_module()
    project = tmp_path / "project"
    global_root = tmp_path / "global"
    project_skill = _write_project_sync_skill(
        project / ".cafe" / "skills", "cafe-race", "2.0.0", "project"
    )
    global_skill = _write_project_sync_skill(
        global_root, "cafe-race", "1.0.0", "global"
    )
    checked = module.compare_skills(project_roots=(project,), global_root=global_root)
    real_copytree = module.shutil.copytree

    def copy_then_mutate(source, destination, **kwargs):
        result = real_copytree(source, destination, **kwargs)
        changed = project_skill if changed_side == "project" else global_skill
        (changed / "SKILL.md").write_text(
            (changed / "SKILL.md").read_text(encoding="utf-8") + "race\n",
            encoding="utf-8",
        )
        return result

    monkeypatch.setattr(module.shutil, "copytree", copy_then_mutate)

    with pytest.raises(module.SkillSyncError, match="changed after approval"):
        module.update_skills(
            project_roots=(project,),
            global_root=global_root,
            selected=("cafe-race",),
            comparison_token=checked["comparison_token"],
        )

    assert "global\n" in (global_skill / "SKILL.md").read_text(encoding="utf-8")


def test_project_global_skill_update_rejects_unapproved_staged_content(
    tmp_path: Path, monkeypatch
) -> None:
    module = _project_global_sync_module()
    project = tmp_path / "project"
    global_root = tmp_path / "global"
    project_skill = _write_project_sync_skill(
        project / ".cafe" / "skills", "cafe-stage-race", "2.0.0", "project"
    )
    global_skill = _write_project_sync_skill(
        global_root, "cafe-stage-race", "1.0.0", "global"
    )
    checked = module.compare_skills(project_roots=(project,), global_root=global_root)
    real_copytree = module.shutil.copytree

    def mutate_then_copy(source, destination, **kwargs):
        (project_skill / "SKILL.md").write_text(
            (project_skill / "SKILL.md").read_text(encoding="utf-8") + "race\n",
            encoding="utf-8",
        )
        return real_copytree(source, destination, **kwargs)

    monkeypatch.setattr(module.shutil, "copytree", mutate_then_copy)

    with pytest.raises(module.SkillSyncError, match="changed after approval while staging"):
        module.update_skills(
            project_roots=(project,),
            global_root=global_root,
            selected=("cafe-stage-race",),
            comparison_token=checked["comparison_token"],
        )

    assert "global\n" in (global_skill / "SKILL.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("second_state", ["existing", "missing"])
def test_project_global_skill_update_rejects_drift_during_multi_skill_publish(
    tmp_path: Path, monkeypatch, second_state: str
) -> None:
    module = _project_global_sync_module()
    project = tmp_path / "project"
    global_root = tmp_path / "global"
    for name in ("cafe-one", "cafe-two"):
        _write_project_sync_skill(
            project / ".cafe" / "skills", name, "2.0.0", f"project {name}"
        )
    _write_project_sync_skill(global_root, "cafe-one", "1.0.0", "global cafe-one")
    if second_state == "existing":
        _write_project_sync_skill(global_root, "cafe-two", "1.0.0", "global cafe-two")
    checked = module.compare_skills(project_roots=(project,), global_root=global_root)
    real_replace = module.os.replace

    def replace_then_mutate(source, target):
        result = real_replace(source, target)
        if Path(source) == global_root / "cafe-one" and Path(target).parent.name == "backups":
            if second_state == "existing":
                skill_file = global_root / "cafe-two" / "SKILL.md"
                skill_file.write_text(
                    skill_file.read_text(encoding="utf-8") + "concurrent\n",
                    encoding="utf-8",
                )
            else:
                _write_project_sync_skill(
                    global_root, "cafe-two", "9.0.0", "concurrent"
                )
        return result

    monkeypatch.setattr(module.os, "replace", replace_then_mutate)

    with pytest.raises(module.SkillSyncError, match="changed after approval during publish"):
        module.update_skills(
            project_roots=(project,),
            global_root=global_root,
            selected=("cafe-one", "cafe-two"),
            comparison_token=checked["comparison_token"],
        )

    assert "global cafe-one" in (global_root / "cafe-one" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "concurrent" in (global_root / "cafe-two" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert list(global_root.glob(".project-skill-sync-*")) == []


def test_project_global_skill_update_times_out_on_contended_lock(
    tmp_path: Path, monkeypatch
) -> None:
    module = _project_global_sync_module()
    global_root = tmp_path / "global"
    global_root.mkdir()
    lock_path = global_root / ".project-skill-sync.lock"
    monkeypatch.setattr(module, "LOCK_TIMEOUT_SECONDS", 0.0)

    with lock_path.open("a+") as holder:
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(module.SkillSyncError, match="timed out waiting"):
            module.update_skills(
                project_roots=(),
                global_root=global_root,
                selected=("cafe-lock",),
                comparison_token="0" * 64,
            )


def test_project_global_skill_update_preserves_backup_when_rollback_fails(
    tmp_path: Path, monkeypatch
) -> None:
    module = _project_global_sync_module()
    project = tmp_path / "project"
    global_root = tmp_path / "global"
    for name in ("cafe-one", "cafe-two"):
        _write_project_sync_skill(
            project / ".cafe" / "skills", name, "2.0.0", f"project {name}"
        )
        _write_project_sync_skill(global_root, name, "1.0.0", f"global {name}")
    checked = module.compare_skills(project_roots=(project,), global_root=global_root)
    real_replace = module.os.replace

    def fail_publish_and_rollback(source, target):
        source_path = Path(source)
        if source_path.parent.name == "staged" and source_path.name == "cafe-two":
            raise OSError("publish blocked")
        if source_path.parent.name == "backups" and source_path.name == "cafe-one":
            raise OSError("rollback blocked")
        return real_replace(source, target)

    monkeypatch.setattr(module.os, "replace", fail_publish_and_rollback)

    with pytest.raises(module.SkillSyncError, match="recover backups from"):
        module.update_skills(
            project_roots=(project,),
            global_root=global_root,
            selected=("cafe-one", "cafe-two"),
            comparison_token=checked["comparison_token"],
        )

    preserved = list(
        global_root.glob(".project-skill-sync-*/backups/cafe-one/SKILL.md")
    )
    assert len(preserved) == 1
    assert "global cafe-one" in preserved[0].read_text(encoding="utf-8")


def test_use_cafe_workflow_skill_makes_driver_own_alignment_decisions() -> None:
    skill = _read_skill_resource("SKILL.md")
    reference = _read_skill_resource("references/handoffs_and_alignment.md")
    normalized = " ".join(reference.split())

    assert "references/handoffs_and_alignment.md" in skill
    assert "## Driver-owned alignment" in reference
    assert "Bundled playbooks omit `alignment:` configuration" in normalized
    assert "`proposal_delta`" in reference
    assert "`strategic_ground`" in reference
    assert "`mandate_level`" in reference
    assert "`relation`" in reference
    assert "`within` + `escalate`: stop" in normalized
    assert "Except for an explicit `escalate` mandate" in normalized
    assert "`within` + `agent`: continue without asking" in normalized
    assert "Do not re-evaluate unchanged scope" in normalized
    assert "checkpoint is evidence, not proof the user must decide" in normalized
    assert "plain text must not approve the checkpoint" in normalized


def test_use_cafe_workflow_uses_structured_human_task_resume_payloads() -> None:
    reference = _read_skill_resource("references/handoffs_and_alignment.md")
    running = _read_skill_resource("references/running_workflow.md")
    normalized = " ".join(reference.split())
    normalized_running = " ".join(running.split())

    assert '"task":"output-review","decision":"confirm"' in reference
    assert '"task":"clarification-answers","answers"' in reference
    assert '"task":"clarification-feedback","feedback"' in reference
    assert '"human_task_id":"<active-human-task-id>"' in reference
    assert "Do not guess or reuse an old task ID" in normalized
    assert "runtime accepts plain text only for a declared `feedback` schema" in normalized
    assert '--user-input "confirmed"' not in reference
    assert "resolve the active HumanTask and its input schema" in normalized_running
    assert "current `human_task_id`" in normalized_running
    assert "Plain text is valid only for a task that explicitly declares" in normalized_running
    assert '--user-input "<confirmed answer or correction>"' not in running


def test_use_cafe_workflow_skill_requires_playbook_derived_kickoff_contract() -> None:
    skill = _read_skill_resource("SKILL.md")
    reference = _read_skill_resource("references/kickoff.md")
    selection = _read_skill_resource("references/playbook_selection.md")
    normalized = " ".join(reference.split())

    assert "references/kickoff.md" in skill
    assert "## Kickoff contract: first blocking gate" in reference
    assert (
        "Before `cafe prepare`, any repository mutation, or the first workflow execution"
        in normalized
    )
    assert "cafe playbook confirmation-gates <playbook-id>" in reference
    assert '`steps.<step>."on".confirm_output`' in reference
    assert "Do not reuse another issue's contract" in normalized
    assert "union to equal the candidates" in normalized
    assert "driver_confirmable" in reference
    assert "reactive interruptions, not scheduled candidates" in normalized
    assert "Alignment is a proactive driver decision" in normalized
    assert "alignment_policy:" not in reference
    assert "alignment_checkpoint: driver_resolvable_when_clear" in reference
    assert "`repository_content_locale`" in reference
    assert "explicitly ask the user to confirm `repository_content_locale`" in normalized
    assert "do not treat inference or a playbook locale as confirmation" in normalized
    assert "repository_language:" in reference
    assert ".cafe/issues/<issue-name>/issue.yaml" in reference
    assert "scripts/format_kickoff_contract.py" in reference
    assert "playbook_selection_rationale" in reference
    assert "independent-QA decision" in reference
    assert "cafe playbook list" in selection
    assert "cafe playbook show <id>" in selection
    assert "repository instructions require an independent QA" in selection
    assert "closest plausible alternative" in selection
    assert "do not infer behavior from a playbook name" in " ".join(selection.split())
    assert "every phase, role, skill, scheduled gate" in normalized
    assert "one primary and zero or more explicitly confirmed fallbacks" in normalized
    assert "model-adjustment authority" in normalized
    assert '--risk-factor "<risk factor; repeat as needed>"' in reference
    assert '--assessment-rationale "<repository evidence for nature and scale>"' in reference


def test_kickoff_contract_formatter_lists_all_phases_and_confirmation_owners(
    tmp_path: Path,
) -> None:
    strategic_context = tmp_path / "strategic_context.yaml"
    strategic_context.write_text(
        """\
version: 1
mandate:
  preset: technical-led
  playbook_id: standard
  axes:
    product_scope: {level: escalate, grounds: [roadmap, positioning]}
    technical: {level: agent, grounds: [engineering_guidelines]}
  out_of_mandate: [pricing, production deploy approval]
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        _kickoff_formatter_command(strategic_context),
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "## Kickoff Contract — issue346" in result.stdout
    assert (
        "| playbook_selection_rationale | Repository policy requires the standard graph; "
        "QA is not independently required, so standard-qa is unnecessary. |" in result.stdout
    )
    assert "| spec | pm | cafe-spec | 是 | driver（驗證後繼續） | 否 |" in result.stdout
    assert "| plan | developer | cafe-plan | 是 | driver（驗證後繼續） | 否 |" in result.stdout
    assert "| develop | developer | cafe-develop | 否 | — | 否 |" in result.stdout
    assert "| review | reviewer | cafe-review | 否 | — | 否 |" in result.stdout
    assert "| pr | developer | cafe-pr | 否 | — | 否 |" in result.stdout
    assert "| effective_locale | zh-TW (user thread override) |" in result.stdout
    assert "| repository_content_locale | zh-TW |" in result.stdout
    assert "| issue_nature | feature/integration |" in result.stdout
    assert "| issue_scale | medium |" in result.stdout
    assert "| model_adjustment_authority | driver_autonomous |" in result.stdout
    assert "| driver_execution.mode | continuous |" in result.stdout
    assert "| driver_execution.poll_interval_seconds | 180 |" in result.stdout
    assert "### Phase model chains — driver-assessed" in result.stdout
    assert (
        "| develop | copilot:implementation-main | "
        "cursor-agent:implementation-fallback | --phase-chain | balanced:" in result.stdout
    )
    assert (
        "| review | gemini:review-main | copilot:review-fallback | --phase-chain | frontier:" in result.stdout
    )
    assert (
        "| pr | cursor-agent:publication-main | gemini:publication-fallback | --phase-chain | efficiency:"
        in result.stdout
    )
    assert (
        "| review | cafe-review | review | high | correctness, security | "
        "equivalent_or_stronger | declared |" in result.stdout
    )
    assert "| need_clarification | user_required | 否 |" in result.stdout
    assert "| product_scope | escalate | roadmap, positioning |" in result.stdout


def test_kickoff_contract_formatter_accepts_driver_execution_overrides(
    tmp_path: Path,
) -> None:
    strategic_context = tmp_path / "strategic_context.yaml"
    strategic_context.write_text(
        """\
version: 1
mandate:
  preset: technical-led
  playbook_id: standard
  axes: {}
  out_of_mandate: []
""",
        encoding="utf-8",
    )

    result = subprocess.run(
        _kickoff_formatter_command(
            strategic_context,
            "--execution-mode",
            "single_step",
            "--poll-interval-seconds",
            "60",
        ),
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "| driver_execution.mode | single_step |" in result.stdout
    assert "| driver_execution.poll_interval_seconds | 60 |" in result.stdout


@pytest.mark.parametrize(
    ("extra_args", "expected_error"),
    [
        (("--execution-mode", "invalid"), "invalid choice"),
        (("--poll-interval-seconds", "0"), "must be greater than zero"),
        (("--poll-interval-seconds", "-1"), "must be greater than zero"),
        (
            ("--poll-interval-seconds", "1.5"),
            "must be an integer number of seconds",
        ),
    ],
)
def test_kickoff_contract_formatter_rejects_invalid_driver_execution(
    tmp_path: Path, extra_args: tuple[str, ...], expected_error: str
) -> None:
    strategic_context = tmp_path / "strategic_context.yaml"
    strategic_context.write_text("version: 1\n", encoding="utf-8")

    result = subprocess.run(
        _kickoff_formatter_command(strategic_context, *extra_args),
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert expected_error in result.stderr


def test_kickoff_formatter_documents_structural_validation_boundary() -> None:
    script = (SKILL_ROOT / "scripts" / "format_kickoff_contract.py").read_text(
        encoding="utf-8"
    )
    kickoff = (SKILL_ROOT / "references" / "kickoff.md").read_text(encoding="utf-8")

    assert "structurally validated" in script
    assert "validates chain structure only; it does not validate model suitability" in kickoff
    assert "driver-assessed" in script


def test_kickoff_contract_formatter_accepts_primary_only_chains(tmp_path: Path) -> None:
    strategic_context = tmp_path / "strategic_context.yaml"
    strategic_context.write_text(
        "mandate: {preset: technical-led, axes: {}, out_of_mandate: []}\n",
        encoding="utf-8",
    )
    rationales = {
        step: "User explicitly selected a primary-only chain; failures stop for adjustment."
        for step in PRIMARY_ONLY_PHASE_CHAINS
    }

    result = subprocess.run(
        [
            sys.executable,
            str(SKILL_ROOT / "scripts" / "format_kickoff_contract.py"),
            "standard",
            "--issue-name",
            "issue-primary-only",
            "--playbook-rationale",
            "The confirmed repository contract selects standard without independent QA.",
            "--issue-nature",
            "localized defect",
            "--issue-scale",
            "small",
            "--model-adjustment-authority",
            "user_approval_required",
            "--risk-factor",
            "none",
            "--assessment-rationale",
            "Focused behavior with bounded verification.",
            *_phase_chain_args(PRIMARY_ONLY_PHASE_CHAINS),
            *_phase_rationale_args(rationales),
            "--repository-content-locale",
            "en-US",
            "--user-required",
            "spec",
            "plan",
            "--current-checkout",
            "--strategic-context",
            str(strategic_context),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "| develop | claude:implementation-main | — | --phase-chain |" in result.stdout


def test_phase_writer_installs_exact_confirmed_chains_atomically(tmp_path: Path) -> None:
    chains = tmp_path / "chains.json"
    target = tmp_path / ".cafe" / "phases.yaml"
    chains.write_text(
        json.dumps(
            {
                "develop": {
                    "name": "David",
                    "role": "developer",
                    "clis": [
                        {"cli": "codex", "model": "gpt-5.6-sol"},
                        {"cli": "claude", "model": "claude-opus-5"},
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    script = SKILL_ROOT / "scripts" / "write_phase_config.py"

    result = subprocess.run(
        [sys.executable, str(script), "--chains-json", str(chains), "--target", str(target)],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    from cafe.utils.phase_config import load_phase_step_model

    resolution = load_phase_step_model(step_name="develop", local_path=target)
    assert resolution.clis == (
        ("codex", "gpt-5.6-sol"),
        ("claude", "claude-opus-5"),
    )


def test_phase_writer_accepts_primary_only_chain(tmp_path: Path) -> None:
    chains = tmp_path / "chains.json"
    target = tmp_path / ".cafe" / "phases.yaml"
    chains.write_text(
        json.dumps(
            {
                "develop": {
                    "name": "David",
                    "role": "developer",
                    "clis": [{"cli": "claude", "model": "claude-opus-5"}],
                }
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SKILL_ROOT / "scripts" / "write_phase_config.py"),
            "--chains-json",
            str(chains),
            "--target",
            str(target),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    from cafe.utils.phase_config import load_phase_step_model

    resolution = load_phase_step_model(step_name="develop", local_path=target)
    assert resolution.clis == (("claude", "claude-opus-5"),)


def test_phase_writer_preserves_existing_file_when_candidate_is_invalid(tmp_path: Path) -> None:
    chains = tmp_path / "chains.json"
    target = tmp_path / ".cafe" / "phases.yaml"
    target.parent.mkdir()
    original = "develop:\n  clis:\n    - {cli: codex, model: old}\n    - {cli: claude, model: old}\n"
    target.write_text(original, encoding="utf-8")
    chains.write_text(
        json.dumps({"develop": {"clis": [{"cli": "codex", "model": "only-primary"}]}}),
        encoding="utf-8",
    )
    script = SKILL_ROOT / "scripts" / "write_phase_config.py"

    result = subprocess.run(
        [sys.executable, str(script), "--chains-json", str(chains), "--target", str(target)],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert target.read_text(encoding="utf-8") == original


def test_phase_writer_rejects_missing_agent_name(tmp_path: Path) -> None:
    chains = tmp_path / "chains.json"
    target = tmp_path / ".cafe" / "phases.yaml"
    chains.write_text(
        json.dumps(
            {
                "develop": {
                    "role": "developer",
                    "clis": [
                        {"cli": "codex", "model": "gpt-5.6-sol"},
                        {"cli": "claude", "model": "claude-opus-5"},
                    ],
                }
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SKILL_ROOT / "scripts" / "write_phase_config.py"),
            "--chains-json",
            str(chains),
            "--target",
            str(target),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "non-empty agent name" in result.stderr
    assert not target.exists()


def test_phase_writer_preserves_existing_file_when_atomic_replace_fails(
    tmp_path: Path, monkeypatch
) -> None:
    chains = tmp_path / "chains.json"
    target = tmp_path / ".cafe" / "phases.yaml"
    target.parent.mkdir()
    original = "develop:\n  clis:\n    - {cli: codex, model: old}\n"
    target.write_text(original, encoding="utf-8")
    chains.write_text(
        json.dumps(
            {
                "develop": {
                    "name": "David",
                    "clis": [
                        {"cli": "codex", "model": "gpt-5.6-sol"},
                        {"cli": "claude", "model": "claude-opus-5"},
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    script = SKILL_ROOT / "scripts" / "write_phase_config.py"
    spec = importlib.util.spec_from_file_location("test_write_phase_config", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("blocked")))

    try:
        module.write_phase_config(chains_file=chains, target=target)
    except OSError:
        pass
    else:
        raise AssertionError("atomic replacement failure must be surfaced")

    assert target.read_text(encoding="utf-8") == original
    assert not list(target.parent.glob(".phases.yaml.*.tmp"))


def test_preflight_cache_reuses_only_success_for_same_cli_fingerprint(
    tmp_path: Path, monkeypatch
) -> None:
    executable_dir = tmp_path / "bin"
    executable_dir.mkdir()
    executable = executable_dir / "codex"
    executable.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then printf '%s\\n' 'codex 1.0'; exit 0; fi\n"
        "if [ ! -d .git ]; then printf '%s\\n' 'missing disposable git repository' >&2; exit 1; fi\n"
        "printf '%s\\n' "
        "'{\"type\":\"item.completed\",\"item\":{\"type\":\"agent_message\","
        "\"text\":\"CAFE_PREFLIGHT_OK\"}}' "
        "'{\"type\":\"turn.completed\",\"usage\":{}}'\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", f"{executable_dir}{os.pathsep}{os.environ['PATH']}")
    cache_file = tmp_path / "cache" / "preflight.json"

    miss = _run_preflight_cache(
        cache_file, "candidate-check", "--cli", "codex", "--model", "exact-model-v1"
    )
    assert miss.returncode == 3
    assert json.loads(miss.stdout)["status"] == "miss"

    recorded = _run_preflight_cache(
        cache_file,
        "candidate-probe",
        "--cli",
        "codex",
        "--model",
        "exact-model-v1",
    )
    assert recorded.returncode == 0, recorded.stderr
    assert json.loads(recorded.stdout)["status"] == "fresh"
    assert cache_file.stat().st_mode & 0o777 == 0o600

    hit = _run_preflight_cache(
        cache_file,
        "candidate-probe",
        "--cli",
        "codex",
        "--model",
        "exact-model-v1",
    )
    assert hit.returncode == 0, hit.stderr
    assert json.loads(hit.stdout)["status"] == "hit"

    executable.write_text(
        "#!/bin/sh\nprintf '%s\\n' 'codex version 2.0'\n", encoding="utf-8"
    )
    executable.chmod(0o755)
    changed = _run_preflight_cache(
        cache_file,
        "candidate-check",
        "--cli",
        "codex",
        "--model",
        "exact-model-v1",
    )
    assert changed.returncode == 3
    assert json.loads(changed.stdout)["reason"] == "not_cached"


def test_preflight_cache_can_invalidate_candidate_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    executable_dir = tmp_path / "bin"
    executable_dir.mkdir()
    executable = executable_dir / "probe-cli"
    executable.write_text("#!/bin/sh\nprintf '%s\\n' 'probe-cli 1.0'\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(executable_dir))
    cache_file = tmp_path / "preflight.json"

    module = _load_script_module(
        SKILL_ROOT / "scripts" / "preflight_cache.py", "preflight_cache_invalidation"
    )
    recorded = module.candidate_record(
        cache_file=cache_file,
        cli="probe-cli",
        model="exact-model",
        resolved_model=None,
        now=1.0,
    )
    assert recorded["status"] == "recorded"
    invalidated = _run_preflight_cache(
        cache_file,
        "candidate-invalidate",
        "--cli",
        "probe-cli",
        "--model",
        "exact-model",
    )
    assert invalidated.returncode == 0, invalidated.stderr
    assert json.loads(invalidated.stdout)["removed"] == 1
    miss = _run_preflight_cache(
        cache_file,
        "candidate-check",
        "--cli",
        "probe-cli",
        "--model",
        "exact-model",
    )
    assert miss.returncode == 3


def test_preflight_cache_runs_and_reuses_cafe_fallback_smoke(tmp_path: Path) -> None:
    cache_file = tmp_path / "preflight.json"
    args = (
        "fallback-smoke",
        "--entry",
        "codex:primary-model",
        "--entry",
        "claude:fallback-model",
    )

    fresh = _run_preflight_cache(cache_file, *args)
    assert fresh.returncode == 0, fresh.stderr
    assert json.loads(fresh.stdout)["status"] == "fresh"
    hit = _run_preflight_cache(cache_file, *args)
    assert hit.returncode == 0, hit.stderr
    assert json.loads(hit.stdout)["status"] == "hit"
    forced = _run_preflight_cache(cache_file, *args, "--force")
    assert forced.returncode == 0, forced.stderr
    assert json.loads(forced.stdout)["status"] == "fresh"


def test_kickoff_contract_formatter_rejects_incomplete_gate_partition(
    tmp_path: Path,
) -> None:
    strategic_context = tmp_path / "strategic_context.yaml"
    strategic_context.write_text(
        "mandate: {preset: technical-led, axes: {}, out_of_mandate: []}\n",
        encoding="utf-8",
    )
    script = (
        PROJECT_ROOT
        / "src"
        / "cafe"
        / "data"
        / "skills"
        / "use-cafe-workflow"
        / "scripts"
        / "format_kickoff_contract.py"
    )

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "standard",
            "--issue-name",
            "issue346",
            "--playbook-rationale",
            "The confirmed issue contract selects standard.",
            "--issue-nature",
            "localized defect",
            "--issue-scale",
            "small",
            "--model-adjustment-authority",
            "user_approval_required",
            "--risk-factor",
            "none",
            "--assessment-rationale",
            "One localized behavior and focused tests.",
            "--repository-content-locale",
            "en-US",
            "--user-required",
            "spec",
            "--worktree",
            ".cafe/worktrees/issue346",
            "--strategic-context",
            str(strategic_context),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "unassigned gates: plan" in result.stderr


def test_kickoff_contract_formatter_uses_cafe_python_when_site_packages_are_missing(
    tmp_path: Path,
) -> None:
    strategic_context = tmp_path / "strategic_context.yaml"
    strategic_context.write_text(
        "mandate: {preset: technical-led, axes: {}, out_of_mandate: []}\n",
        encoding="utf-8",
    )
    script = (
        PROJECT_ROOT
        / "src"
        / "cafe"
        / "data"
        / "skills"
        / "use-cafe-workflow"
        / "scripts"
        / "format_kickoff_contract.py"
    )

    result = subprocess.run(
        [
            sys.executable,
            "-S",
            str(script),
            "standard",
            "--issue-name",
            "issue346",
            "--playbook-rationale",
            "The confirmed issue contract selects standard.",
            "--issue-nature",
            "localized defect",
            "--issue-scale",
            "small",
            "--model-adjustment-authority",
            "user_approval_required",
            "--risk-factor",
            "none",
            "--assessment-rationale",
            "One localized behavior and focused tests.",
            "--repository-content-locale",
            "en-US",
            "--user-required",
            "spec",
            "plan",
            "--worktree",
            ".cafe/worktrees/issue346",
            "--strategic-context",
            str(strategic_context),
            *_phase_chain_args(),
            *_phase_rationale_args(),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "## Kickoff Contract — issue346" in result.stdout
    assert "| spec | pm | cafe-spec | yes | user | yes |" in result.stdout


def test_kickoff_formatter_resolves_custom_playbook_iteration_skills(
    tmp_path: Path,
) -> None:
    skills_root = tmp_path / ".cafe" / "skills"
    playbooks_root = tmp_path / ".cafe" / "playbooks"
    playbooks_root.mkdir(parents=True)
    for name, profile in {
        "cafe-audit_first": """workload: research
    reasoning: standard
    risk_domains: [evidence]
    fallback_strength: equivalent""",
        "cafe-audit_revise": """workload: review
    reasoning: high
    risk_domains: [security]
    fallback_strength: equivalent_or_stronger""",
    }.items():
        skill_dir = skills_root / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"""---
name: {name}
description: Custom audit phase.
workflow:
  execution_profile:
    {profile}
---

# Audit
""",
            encoding="utf-8",
        )
    (playbooks_root / "custom-audit.yaml").write_text(
        """playbook:
  id: custom-audit
  conversation_locale: en-US
roles:
  auditor: {default_agent: Ada}
steps:
  audit:
    skill: {'1': cafe-audit_first, default: cafe-audit_revise}
    role: auditor
    assignee_type: agent
    input_artifacts: []
    output_artifact: report
    'on': {await_agent: _done}
entry_point: audit
""",
        encoding="utf-8",
    )
    strategic_context = tmp_path / ".cafe" / "strategic_context.yaml"
    strategic_context.write_text(
        "mandate: {preset: technical-led, axes: {}, out_of_mandate: []}\n",
        encoding="utf-8",
    )
    script = SKILL_ROOT / "scripts" / "format_kickoff_contract.py"

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "custom-audit",
            "--project-root",
            str(tmp_path),
            "--issue-name",
            "audit-1",
            "--playbook-rationale",
            "The user selected the custom audit graph; no builtin candidate owns this audit responsibility.",
            "--issue-nature",
            "security review",
            "--issue-scale",
            "medium",
            "--model-adjustment-authority",
            "driver_autonomous",
            "--risk-factor",
            "security boundary",
            "--assessment-rationale",
            "The custom phase evaluates a security-sensitive contract.",
            "--phase-chain",
            "audit=gemini:audit-main,copilot:audit-fallback",
            "--phase-rationale",
            "audit=frontier: high security review with an equivalent independent fallback",
            "--repository-content-locale",
            "en-US",
            "--current-checkout",
            "--strategic-context",
            str(strategic_context),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "| audit | auditor | cafe-audit_first, cafe-audit_revise |" in result.stdout
    assert (
        "| audit | cafe-audit_first, cafe-audit_revise | research, review | high | "
        "evidence, security | equivalent_or_stronger | declared |" in result.stdout
    )


def test_kickoff_formatter_rejects_unresolved_phase_models(tmp_path: Path) -> None:
    strategic_context = tmp_path / "strategic_context.yaml"
    strategic_context.write_text(
        "mandate: {preset: technical-led, axes: {}, out_of_mandate: []}\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            str(SKILL_ROOT / "scripts" / "format_kickoff_contract.py"),
            "simple",
            "--project-root",
            str(tmp_path),
            "--issue-name",
            "issue-no-models",
            "--playbook-rationale",
            (
                "The simple graph covers the localized change with independent QA "
                "and no separate plan or code review."
            ),
            "--issue-nature",
            "localized defect",
            "--issue-scale",
            "small",
            "--model-adjustment-authority",
            "user_approval_required",
            "--risk-factor",
            "none",
            "--assessment-rationale",
            "Focused behavior.",
            "--repository-content-locale",
            "en-US",
            "--user-required",
            "spec",
            "--current-checkout",
            "--strategic-context",
            str(strategic_context),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "step='spec'" in result.stderr
    assert "field='spec'" in result.stderr


def test_kickoff_formatter_rejects_missing_phase_rationale(tmp_path: Path) -> None:
    strategic_context = tmp_path / "strategic_context.yaml"
    strategic_context.write_text(
        "mandate: {preset: technical-led, axes: {}, out_of_mandate: []}\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            str(SKILL_ROOT / "scripts" / "format_kickoff_contract.py"),
            "simple",
            "--project-root",
            str(PROJECT_ROOT),
            "--issue-name",
            "issue-no-rationale",
            "--playbook-rationale",
            (
                "The simple graph covers the localized change with independent QA "
                "and no separate plan or code review."
            ),
            "--issue-nature",
            "localized defect",
            "--issue-scale",
            "small",
            "--model-adjustment-authority",
            "user_approval_required",
            "--risk-factor",
            "none",
            "--assessment-rationale",
            "Focused behavior.",
            "--phase-chain",
            "spec=gemini:requirements-main,copilot:requirements-fallback",
            "--repository-content-locale",
            "en-US",
            "--user-required",
            "spec",
            "--current-checkout",
            "--strategic-context",
            str(strategic_context),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "missing phase rationale for agent-executed step: spec" in result.stderr


def test_builtin_confirmation_gate_candidates_come_from_playbook_declarations() -> None:
    loader = PlaybookLoader(project_root=PROJECT_ROOT)

    actual = {
        playbook_id: confirmation_gate_steps(loader.load_model(playbook_id).model)
        for playbook_id in (
            "direct",
            "simple",
            "standard",
            "standard-qa",
            "tdd",
            "tdd-qa",
            "editorial",
            "hotfix",
            "incident",
            "research",
        )
    }

    assert actual == {
        "direct": (),
        "simple": ("spec",),
        "standard": ("spec", "plan"),
        "standard-qa": ("spec", "plan"),
        "tdd": ("spec", "plan"),
        "tdd-qa": ("spec", "plan"),
        "editorial": ("brief",),
        "hotfix": (),
        "incident": (),
        "research": (),
    }


def test_bundled_playbooks_do_not_delegate_alignment_judgment_to_core() -> None:
    loader = PlaybookLoader(project_root=PROJECT_ROOT)

    for playbook_id in (
        "direct",
        "simple",
        "standard",
        "standard-qa",
        "tdd",
        "tdd-qa",
        "editorial",
        "hotfix",
        "incident",
        "research",
    ):
        playbook = loader.load_model(playbook_id).model
        for step in playbook.steps.values():
            assert step.alignment is None
            assert "alignment_checkpoint" not in step.on
            assert "alignment_checkpoint" not in step.valid_intents
            assert "AlignmentCheckpointGate" not in step.hooks.prepare_input
            step_def = step.model_dump(by_alias=True)
            assert PhaseStatusCode.ALIGNMENT_CHECKPOINT not in effective_step_status_codes(step_def)
            assert "alignment_checkpoint" not in effective_step_handoff_intents(step_def)
            assert (
                GenericPhase._detect_status_code(
                    response="alignment_checkpoint",
                    step_def=step_def,
                )
                is None
            )


def test_use_cafe_workflow_skill_protects_issue_overrides() -> None:
    skill = _read_skill_resource("SKILL.md")
    reference = _read_skill_resource("references/strategic_context.md")
    normalized = " ".join(reference.split())

    assert "references/strategic_context.md" in skill
    assert "## Protected issue overrides" in reference
    assert "optional, protected overrides" in normalized
    assert "Do not create `issues.<issue-name>` because" in reference
    assert "Do not store workflow progress, baton state, phase outputs" in reference
    assert "Do not add, edit, or remove an issue override" in normalized
    assert "Leave `issues:` untouched unless the user explicitly requested" in reference


def test_use_cafe_workflow_bounds_diagnosis_and_repairs_only_declarative_layers() -> None:
    skill = _read_skill_resource("SKILL.md")
    reference = _read_skill_resource("references/diagnosis_and_repair.md")
    normalized = " ".join(reference.split())

    assert "references/diagnosis_and_repair.md" in skill
    assert "# Bounded Diagnosis And Repair" in reference
    assert "Playbook declarative defect" in reference
    assert "Phase declarative defect" in reference
    assert "Driver or CAFE core defect" in reference
    assert "activate `write-cafe-playbook`" in normalized
    assert "activate `write-cafe-phase`" in normalized
    assert "Do not invent a `write-cafe-driver` skill" in normalized
    assert "Search open and closed issues read-only" in reference
    assert "https://github.com/luyotw/cafe/issues" in reference
    assert "Do not create, comment on, or close an upstream issue" in normalized
    assert "stale installed skills" in normalized
    assert "unconfirmed or transient failures" in normalized


def test_use_cafe_workflow_prefers_user_conversation_locale() -> None:
    skill = _read_skill_resource("SKILL.md")
    reference = _read_skill_resource("references/kickoff.md")
    normalized = " ".join(reference.split())

    assert "references/kickoff.md" in skill
    assert "## Conversation locale checklist" in reference
    assert "playbook.conversation_locale" in reference
    assert "cafe playbook confirmation-gates <playbook-id>" in reference
    assert "`Conversation locale:` line" in normalized
    assert "a locale the user directly requested for this thread" in normalized
    assert "a locale reliably inferred from the user's own natural-language messages" in normalized
    assert "Do not infer from quoted text, pasted artifacts, code, commands" in normalized
    assert "If the evidence is mixed or ambiguous, use the playbook locale" in normalized
    assert "For `auto`, infer from the user's messages using the same rules above" in normalized
    assert "explicit BCP 47 value as the fallback" in normalized
    assert "conversation_locale: zh-TW (inferred user preference from current thread)" in normalized
    assert "conversation_locale: en-US (from playbook: standard)" in normalized
    assert "required kickoff field, not a confirmation gate" in normalized
    assert "asking why a language was used is not an override" in normalized
    assert "Never claim this skill lacks a locale rule" in normalized
    assert "Do not copy the locale into `issue.yaml`" in normalized
    assert "commands, paths, playbook and step names, intents, artifact keys" in normalized


def test_use_cafe_workflow_requires_confirmed_repository_content_locale() -> None:
    skill = _read_skill_resource("SKILL.md")
    reference = _read_skill_resource("references/kickoff.md")
    normalized_skill = " ".join(skill.split())
    normalized = " ".join(reference.split())

    assert "repository content locale used by documentation and code comments" in normalized_skill
    assert "## Repository content locale checklist" in reference
    assert "Before `cafe init` or any other repository mutation" in normalized
    assert "explicitly ask the user to confirm `repository_content_locale`" in normalized
    assert (
        "Use one repository content locale for both documentation and code comments" in normalized
    )
    assert (
        "scoped exception instead of making two languages a routine kickoff decision" in normalized
    )
    assert "Acceptance of the complete kickoff contract explicitly confirms it" in normalized
    assert "Persist the confirmed value in `.cafe/strategic_context.yaml`" in normalized
    assert "not in issue-owned workflow state" in normalized


def test_use_cafe_workflow_requires_driver_execution_contract_and_model_authority() -> None:
    skill = _read_skill_resource("SKILL.md")
    kickoff = _read_skill_resource("references/kickoff.md")
    running = _read_skill_resource("references/running_workflow.md")
    models = _read_skill_resource("references/model_selection.md")
    normalized_kickoff = " ".join(kickoff.split())
    normalized_running = " ".join(running.split())
    normalized_models = " ".join(models.split())

    assert "references/model_selection.md" in skill
    assert "`driver_execution` contract" in skill
    assert "`continuous` by default" in skill
    assert "may be `single_step`" in skill
    assert "cafe workflow --execute --mute-agent-output" in skill
    assert "provider narration is parsed and persisted" in normalized_running
    assert "does not suppress workflow lifecycle events" in normalized_running
    assert "Remove it only when the user requests a live transcript" in normalized_running
    assert "persisted baton without forcing `--start-step`" in skill
    assert "Do not use `cafe make` for driver execution" in normalized_running
    assert "required cadence" in normalized_running
    assert "Start the timer when the process starts or resumes" in normalized_running
    assert (
        "perform one proactive inspection when the interval elapses" in normalized_running
    )
    assert "then restart the timer" in normalized_running
    assert (
        "if execution remains active after handling the signal, restart the timer"
        in normalized_running
    )
    assert "Stop polling when the command exits" in normalized_running
    assert "must not trigger an extra CAFE status or artifact poll" in normalized_running
    assert "If the mapping is missing, return to `kickoff.md`" in normalized_running
    assert (
        "Start the timer when a workflow process starts or resumes" in normalized_kickoff
    )
    assert (
        "perform one proactive inspection when the interval elapses, then restart the timer"
        in normalized_kickoff
    )
    assert (
        "if the process remains active afterward, restart the timer"
        in normalized_kickoff
    )
    assert "Stop the timer when the command exits" in normalized_kickoff
    assert "must not trigger extra workflow polling" in normalized_kickoff
    assert (
        "For an older prepared issue without `driver_execution`, propose `continuous` and "
        "`180` as defaults and confirm them before the next execution"
        in normalized_kickoff
    )
    assert "do not silently infer that the older driver used either mode" in normalized_kickoff
    assert "`model_adjustment_authority`" in kickoff
    assert "`driver_autonomous`" in kickoff
    assert "`user_approval_required`" in kickoff
    assert "No provider or model is built into this skill" in normalized_models
    assert "The phase skill owns only its provider-neutral minimum execution profile" in normalized_models
    assert "The driver owns the capability-band classification" in normalized_models
    assert "`efficiency`" in models
    assert "`balanced`" in models
    assert "`frontier`" in models
    assert "Treat an existing repository chain as a candidate, not as selection evidence" in normalized_models
    assert "A publication phase may remain `efficiency`" in normalized_models
    assert "Model release order is not capability-band order" in normalized_models
    assert "A user may choose a primary-only chain" in normalized_models
    assert "A primary-only chain skips fallback smoke" in normalized_models
    assert "a primary failure is a hard stop" in normalized_models
    assert "Never reject a fallback solely because its version number is lower" in normalized_models
    assert "never assume two versions are equivalent solely because they share a model family" in normalized_models
    assert "Reduced uncertainty after spec or plan may move" in normalized_models
    assert "Resolve the skill bound by the active playbook" in normalized_models
    assert "actual remaining iteration" in normalized_models
    assert "making the primary fail with the classified `model_not_found`" in normalized_models
    assert "Do not create a fake failure inside a live issue" in normalized_models
    assert "scripts/preflight_cache.py" in models
    assert "last 24 hours" in normalized_models
    assert "successful result for 30 days" in normalized_models
    assert "never records a failed" in normalized_models
    assert "A live workflow failure always overrides cached evidence" in normalized_models
    assert (SKILL_ROOT / "scripts" / "preflight_cache.py").is_file()
    assert "Reassess at contract-defined boundaries" in normalized_models
    assert "In `continuous` mode, do not stop execution" in normalized_models
    assert "In `single_step` mode, reassess after every completed step" in normalized_models
    assert "active worktree's `.cafe/phases.yaml`" in normalized_models


def test_use_cafe_workflow_batches_driver_pr_review_findings() -> None:
    skill = _read_skill_resource("SKILL.md")
    reference = _read_skill_resource("references/convergent_pr_review.md")
    normalized_reference = " ".join(reference.split())

    assert "references/convergent_pr_review.md" in skill
    assert "finish its full review matrix" in skill
    assert "consolidate every currently observable blocker" in normalized_reference
    assert "## 1. Establish one review baseline" in reference
    assert (
        "every acceptance criterion and the original reported production journey"
        in normalized_reference
    )
    assert "real production entry points and callers" in normalized_reference
    assert "Continue across all applicable rows after finding a blocker" in reference
    assert "A green unit/full suite is supporting evidence" in normalized_reference
    assert "tests do not forge workflow output, trusted state, or receipts" in normalized_reference
    assert "previously observable but missed" in reference
    assert "Do not restart repository-wide discovery for unchanged areas" in reference
