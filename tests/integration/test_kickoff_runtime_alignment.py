"""Installed Manager helpers must discover the same checkout catalog as CAFE."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_ROOT = PROJECT_ROOT / "src/cafe/data/skills/use-cafe-workflow/scripts"


@pytest.fixture
def installed_helper(tmp_path):
    """Make script-relative paths and an older importable install misleading."""
    scripts = tmp_path / "installed-helper/scripts"
    shutil.copytree(SCRIPT_ROOT, scripts, ignore=shutil.ignore_patterns("__pycache__"))
    stale = tmp_path / "old-install/cafe"
    stale.mkdir(parents=True)
    (stale / "__init__.py").write_text(
        'raise RuntimeError("stale CAFE runtime was imported")\n', encoding="utf-8"
    )
    env = dict(
        os.environ,
        PYTHONPATH=str(stale.parent),
        XDG_CONFIG_HOME=str(tmp_path / "config"),
        XDG_CACHE_HOME=str(tmp_path / "cache"),
    )
    return scripts, env


@pytest.mark.parametrize("external_cwd", [False, True])
def test_discovery_uses_requested_checkout_before_importing_stale_runtime(
    tmp_path, installed_helper, external_cwd
):
    scripts, env = installed_helper
    request = tmp_path / "request.json"
    request.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "project_root": str(PROJECT_ROOT),
                "issue_name": "alignment-fixture",
            }
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            str(scripts / "prepare_kickoff.py"),
            "discover",
            "--request-file",
            str(request),
        ],
        cwd=tmp_path if external_cwd else PROJECT_ROOT / "src",
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    catalog = json.loads(result.stdout)["catalog"]
    candidates = {item["id"]: item for item in catalog["candidates"]}
    for name in ("subagent-flow", "subagent-flow-qa"):
        assert candidates[name]["eligible"]
        assert candidates[name]["applicability"]["summary"]
        assert "profiles" not in candidates[name]  # Discovery defers detailed skill loading.
        assert Path(candidates[name]["path"]).is_relative_to(PROJECT_ROOT)


def test_formatter_aligns_runtime_before_dependency_imports(tmp_path, installed_helper):
    scripts, env = installed_helper
    result = subprocess.run(
        [
            sys.executable,
            str(scripts / "format_kickoff_contract.py"),
            "--project-root",
            str(PROJECT_ROOT),
            "--help",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "--phase-chain" in result.stdout


def test_checkout_cwd_is_used_without_an_explicit_project(installed_helper):
    scripts, env = installed_helper
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from _runtime_bootstrap import align_checkout_runtime; "
            "align_checkout_runtime([]); import cafe; print(cafe.__file__)",
        ],
        cwd=PROJECT_ROOT / "src",
        env={**env, "PYTHONPATH": os.pathsep.join([str(scripts), env["PYTHONPATH"]])},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()) == PROJECT_ROOT / "src/cafe/__init__.py"


def test_ordinary_project_keeps_installed_runtime(tmp_path, installed_helper):
    scripts, env = installed_helper
    package = Path(env["PYTHONPATH"]) / "cafe/__init__.py"
    package.write_text('identity = "installed-runtime"\n', encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from _runtime_bootstrap import align_checkout_runtime; "
            "align_checkout_runtime([]); import cafe; print(cafe.identity)",
        ],
        cwd=tmp_path,
        env={**env, "PYTHONPATH": os.pathsep.join([str(scripts), env["PYTHONPATH"]])},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "installed-runtime"


def test_preloaded_wrong_runtime_fails_without_mixing_modules(tmp_path, installed_helper):
    scripts, env = installed_helper
    package = Path(env["PYTHONPATH"]) / "cafe/__init__.py"
    package.write_text('identity = "installed-runtime"\n', encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import cafe; from _runtime_bootstrap import align_checkout_runtime; "
            f"align_checkout_runtime(['--project-root', {str(PROJECT_ROOT)!r}])",
        ],
        cwd=tmp_path,
        env={**env, "PYTHONPATH": os.pathsep.join([str(scripts), env["PYTHONPATH"]])},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0
    assert "CAFE runtime already loaded" in result.stderr


@pytest.mark.parametrize("bundled", [True, False])
@pytest.mark.parametrize("external_runtime", ["stale", "absent"])
def test_native_delegation_entry_bootstraps_requested_checkout_before_imports(
    tmp_path, installed_helper, bundled, external_runtime
):
    scripts, env = installed_helper
    if external_runtime == "absent":
        # An importable package lacking CAFE's runtime represents an absent API.
        (Path(env["PYTHONPATH"]) / "cafe/__init__.py").write_text("", encoding="utf-8")
    script = (scripts if bundled else SCRIPT_ROOT) / "native_delegation_accounting.py"
    result = subprocess.run(
        [sys.executable, str(script), "--project-root", str(PROJECT_ROOT), "--help"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "--correlation" in result.stdout and "--fresh-facts" in result.stdout


def test_installed_native_delegation_bundle_keeps_current_runtime_for_ordinary_project(
    tmp_path, installed_helper
):
    scripts, env = installed_helper
    result = subprocess.run(
        [
            sys.executable,
            str(scripts / "native_delegation_accounting.py"),
            "--project-root",
            str(tmp_path),
            "--help",
        ],
        cwd=tmp_path,
        env={**env, "PYTHONPATH": str(PROJECT_ROOT / "src")},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("bundled", [True, False])
def test_native_delegation_entry_uses_cli_owning_interpreter_without_pythonpath(
    tmp_path, installed_helper, bundled
):
    cli = shutil.which("cafe")
    if cli is None:
        pytest.skip("CAFE CLI entry is not installed in this test environment")
    shebang = Path(cli).read_text(encoding="utf-8").splitlines()[0]
    if not shebang.startswith("#!/") or " " in shebang:
        pytest.skip("CLI does not declare a direct Python interpreter")
    scripts, env = installed_helper
    env.pop("PYTHONPATH", None)
    script = (scripts if bundled else SCRIPT_ROOT) / "native_delegation_accounting.py"
    result = subprocess.run(
        [shebang[2:], str(script), "--project-root", str(PROJECT_ROOT), "--help"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "--correlation" in result.stdout
