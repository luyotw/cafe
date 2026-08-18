"""Tests for the repository-owned, agent-neutral installation bootstrap."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from cafe.install import bootstrap
from cafe.install.bootstrap import BootstrapError, create_plan, install


def _source_checkout(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    (source / "src" / "cafe").mkdir(parents=True)
    (source / "pyproject.toml").write_text(
        '[project]\nname = "cafe-engine"\n',
        encoding="utf-8",
    )
    return source


def _plan(tmp_path: Path, source: Path, generation: str = "env-test"):
    return create_plan(
        source=source,
        home_dir=tmp_path / "home",
        install_root=tmp_path / "install",
        bin_dir=tmp_path / "bin",
        generation=generation,
    )


def test_create_plan_is_user_scoped_and_side_effect_free(tmp_path: Path) -> None:
    source = _source_checkout(tmp_path)

    plan = _plan(tmp_path, source)

    assert plan.environment == tmp_path / "install" / "environments" / "env-test"
    assert plan.launcher == tmp_path / "bin" / "cafe"
    assert plan.cafe_executable == plan.environment / "bin" / "cafe"
    assert not plan.install_root.exists()
    assert not plan.bin_dir.exists()


def test_install_publishes_managed_launcher_manifest_and_replaces_previous_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source_checkout(tmp_path)
    previous_plan = _plan(tmp_path, source, "env-previous")
    previous_plan.cafe_executable.parent.mkdir(parents=True)
    previous_plan.cafe_executable.write_text("old", encoding="utf-8")
    previous_plan.bin_dir.mkdir(parents=True)
    previous_plan.launcher.symlink_to(previous_plan.cafe_executable)
    previous_manifest = {
        "environment": str(previous_plan.environment),
        "cafe_version": "0.2.0",
    }
    previous_plan.install_root.mkdir(parents=True, exist_ok=True)
    (previous_plan.install_root / "install.json").write_text(
        json.dumps(previous_manifest), encoding="utf-8"
    )
    plan = _plan(tmp_path, source, "env-current")

    def fake_install_environment(current_plan):
        current_plan.cafe_executable.parent.mkdir(parents=True)
        current_plan.cafe_executable.write_text("new", encoding="utf-8")
        return "0.3.0"

    monkeypatch.setattr(bootstrap, "_install_environment", fake_install_environment)

    version = install(plan)

    assert version == "0.3.0"
    assert plan.launcher.resolve() == plan.cafe_executable
    manifest = json.loads((plan.install_root / "install.json").read_text(encoding="utf-8"))
    assert manifest["cafe_version"] == "0.3.0"
    assert manifest["environment"] == str(plan.environment)
    assert not previous_plan.environment.exists()


def test_environment_install_verifies_package_then_synchronizes_all_skills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source_checkout(tmp_path)
    plan = _plan(tmp_path, source)
    commands: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command, **kwargs):
        command_list = list(command)
        commands.append((command_list, kwargs))
        if command_list[1:3] == ["-m", "venv"]:
            plan.cafe_executable.parent.mkdir(parents=True)
            plan.cafe_executable.write_text("installed", encoding="utf-8")
            environment_python = plan.environment / "bin" / "python"
            environment_python.write_text("python", encoding="utf-8")
        stdout = "CAFE version 0.3.0\n" if command_list[-1] == "version" else ""
        return subprocess.CompletedProcess(command_list, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(bootstrap, "_run", fake_run)

    version = bootstrap._install_environment(plan)

    assert version == "0.3.0"
    assert [command[-2:] for command, _ in commands][-1] == ["skill", "sync-global"]
    assert "--no-cache-dir" in commands[1][0]
    version_kwargs = commands[-2][1]
    assert version_kwargs["cwd"] == plan.install_root
    assert version_kwargs["env"]["CAFE_SKIP_ENTRYPOINT_CHECK"] == "1"
    assert version_kwargs["env"]["CAFE_SKIP_GLOBAL_SKILL_SYNC"] == "1"
    sync_kwargs = commands[-1][1]
    assert "CAFE_SKIP_GLOBAL_SKILL_SYNC" not in sync_kwargs["env"]


def test_install_refuses_to_replace_foreign_launcher(tmp_path: Path) -> None:
    source = _source_checkout(tmp_path)
    plan = _plan(tmp_path, source)
    plan.bin_dir.mkdir(parents=True)
    plan.launcher.write_text("#!/bin/sh\n", encoding="utf-8")

    with pytest.raises(BootstrapError, match="not managed by CAFE"):
        install(plan)

    assert not plan.environment.exists()
    assert plan.launcher.read_text(encoding="utf-8") == "#!/bin/sh\n"


def test_failed_install_removes_only_the_new_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source_checkout(tmp_path)
    plan = _plan(tmp_path, source)

    def fail_install(current_plan):
        current_plan.environment.mkdir(parents=True)
        (current_plan.environment / "partial").write_text("partial", encoding="utf-8")
        raise BootstrapError("verification failed")

    monkeypatch.setattr(bootstrap, "_install_environment", fail_install)

    with pytest.raises(BootstrapError, match="verification failed"):
        install(plan)

    assert not plan.environment.exists()
    assert not plan.launcher.exists()


def test_manifest_failure_restores_previous_launcher_and_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source_checkout(tmp_path)
    previous_plan = _plan(tmp_path, source, "env-previous")
    previous_plan.cafe_executable.parent.mkdir(parents=True)
    previous_plan.cafe_executable.write_text("old", encoding="utf-8")
    previous_plan.bin_dir.mkdir(parents=True)
    previous_plan.launcher.symlink_to(previous_plan.cafe_executable)
    previous_plan.install_root.mkdir(parents=True, exist_ok=True)
    (previous_plan.install_root / "install.json").write_text(
        json.dumps({"environment": str(previous_plan.environment)}), encoding="utf-8"
    )
    plan = _plan(tmp_path, source, "env-current")

    def fake_install_environment(current_plan):
        current_plan.cafe_executable.parent.mkdir(parents=True)
        current_plan.cafe_executable.write_text("new", encoding="utf-8")
        return "0.3.0"

    monkeypatch.setattr(bootstrap, "_install_environment", fake_install_environment)
    monkeypatch.setattr(
        bootstrap,
        "_write_manifest",
        lambda *_: (_ for _ in ()).throw(OSError("manifest unavailable")),
    )

    with pytest.raises(OSError, match="manifest unavailable"):
        install(plan)

    assert previous_plan.environment.exists()
    assert previous_plan.launcher.resolve() == previous_plan.cafe_executable
    assert not plan.environment.exists()


def test_noninteractive_main_requires_explicit_authorization(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = _source_checkout(tmp_path)

    with patch("sys.stdin.isatty", return_value=False):
        result = bootstrap.main(
            [
                "--source",
                str(source),
                "--install-root",
                str(tmp_path / "install"),
                "--bin-dir",
                str(tmp_path / "bin"),
            ]
        )

    assert result == 1
    assert "requires --yes" in capsys.readouterr().err
    assert not (tmp_path / "install").exists()


def test_dry_run_validates_and_prints_plan_without_writing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = _source_checkout(tmp_path)

    result = bootstrap.main(
        [
            "--source",
            str(source),
            "--install-root",
            str(tmp_path / "install"),
            "--bin-dir",
            str(tmp_path / "bin"),
            "--dry-run",
        ]
    )

    assert result == 0
    assert "CAFE user installation plan" in capsys.readouterr().out
    assert not (tmp_path / "install").exists()
