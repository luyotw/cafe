"""Install CAFE from a trusted source checkout into an isolated user environment."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

MINIMUM_PYTHON = (3, 10)
MANAGED_WINDOWS_LAUNCHER = "REM Managed by the CAFE repository bootstrap"


class BootstrapError(RuntimeError):
    """Raised when the bootstrap cannot make a safe user-scoped installation."""


@dataclass(frozen=True)
class BootstrapPlan:
    """Resolved locations for one bootstrap attempt."""

    source: Path
    install_root: Path
    environments_dir: Path
    environment: Path
    bin_dir: Path
    launcher: Path
    cafe_executable: Path


def _default_install_root(home_dir: Path) -> Path:
    configured = os.getenv("XDG_DATA_HOME")
    data_home = Path(configured).expanduser() if configured else home_dir / ".local" / "share"
    return data_home / "cafe-engine"


def _default_bin_dir(home_dir: Path) -> Path:
    configured = os.getenv("XDG_BIN_HOME")
    return Path(configured).expanduser() if configured else home_dir / ".local" / "bin"


def _environment_executable(environment: Path) -> Path:
    if os.name == "nt":
        return environment / "Scripts" / "cafe.exe"
    return environment / "bin" / "cafe"


def _launcher_path(bin_dir: Path) -> Path:
    return bin_dir / ("cafe.cmd" if os.name == "nt" else "cafe")


def create_plan(
    *,
    source: Path,
    home_dir: Optional[Path] = None,
    install_root: Optional[Path] = None,
    bin_dir: Optional[Path] = None,
    generation: Optional[str] = None,
) -> BootstrapPlan:
    """Resolve all paths without writing to disk."""
    resolved_home = (home_dir or Path.home()).expanduser().resolve()
    resolved_source = source.expanduser().resolve()
    resolved_root = (install_root or _default_install_root(resolved_home)).expanduser().resolve()
    resolved_bin = (bin_dir or _default_bin_dir(resolved_home)).expanduser().resolve()
    environments_dir = resolved_root / "environments"
    if generation is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        generation = f"env-{timestamp}-{uuid.uuid4().hex[:8]}"
    if not re.fullmatch(r"env-[A-Za-z0-9._-]+", generation):
        raise BootstrapError("The generated environment name is invalid")
    environment = environments_dir / generation
    return BootstrapPlan(
        source=resolved_source,
        install_root=resolved_root,
        environments_dir=environments_dir,
        environment=environment,
        bin_dir=resolved_bin,
        launcher=_launcher_path(resolved_bin),
        cafe_executable=_environment_executable(environment),
    )


def _validate_source(source: Path) -> None:
    pyproject = source / "pyproject.toml"
    package = source / "src" / "cafe"
    if not pyproject.is_file() or not package.is_dir():
        raise BootstrapError(
            "Run this bootstrap from a CAFE source checkout containing pyproject.toml and src/cafe"
        )
    metadata = pyproject.read_text(encoding="utf-8")
    if re.search(r'^name\s*=\s*["\']cafe-engine["\']\s*$', metadata, re.MULTILINE) is None:
        raise BootstrapError("The source checkout is not the cafe-engine project")


def _validate_python() -> None:
    if sys.version_info < MINIMUM_PYTHON:
        required = ".".join(str(part) for part in MINIMUM_PYTHON)
        current = f"{sys.version_info.major}.{sys.version_info.minor}"
        raise BootstrapError(f"CAFE requires Python {required}+; this interpreter is {current}")


def _is_managed_posix_launcher(launcher: Path, environments_dir: Path) -> bool:
    if not launcher.is_symlink():
        return False
    target = (launcher.parent / os.readlink(launcher)).resolve(strict=False)
    try:
        relative = target.relative_to(environments_dir.resolve())
    except ValueError:
        return False
    return len(relative.parts) == 3 and relative.parts[-2:] == ("bin", "cafe")


def _is_managed_windows_launcher(launcher: Path) -> bool:
    if not launcher.is_file():
        return False
    try:
        first_line = launcher.read_text(encoding="utf-8").splitlines()[0]
    except (IndexError, OSError, UnicodeError):
        return False
    return first_line == f"@{MANAGED_WINDOWS_LAUNCHER}"


def _validate_launcher(plan: BootstrapPlan) -> None:
    if plan.bin_dir.exists() and not plan.bin_dir.is_dir():
        raise BootstrapError(f"Launcher directory is not a directory: {plan.bin_dir}")
    if not plan.launcher.exists() and not plan.launcher.is_symlink():
        return
    managed = (
        _is_managed_windows_launcher(plan.launcher)
        if os.name == "nt"
        else _is_managed_posix_launcher(plan.launcher, plan.environments_dir)
    )
    if not managed:
        raise BootstrapError(
            f"Refusing to replace an existing launcher not managed by CAFE: {plan.launcher}"
        )


def _run(
    command: Sequence[str],
    *,
    capture_output: bool = False,
    cwd: Optional[Path] = None,
    env: Optional[dict[str, str]] = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        check=True,
        text=True,
        capture_output=capture_output,
        cwd=cwd,
        env=env,
    )


def _install_environment(plan: BootstrapPlan) -> str:
    _run([sys.executable, "-m", "venv", str(plan.environment)])
    environment_python = _environment_python(plan.environment)
    _run(
        [
            str(environment_python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-cache-dir",
            "--no-input",
            str(plan.source),
        ]
    )
    _run([str(environment_python), "-m", "pip", "check"])
    if not plan.cafe_executable.is_file():
        raise BootstrapError(f"The installed CAFE executable is missing: {plan.cafe_executable}")
    cli_env = os.environ.copy()
    cli_env.update(
        {
            "CAFE_SKIP_ENTRYPOINT_CHECK": "1",
            "CAFE_SKIP_GLOBAL_SKILL_SYNC": "1",
            "CAFE_SKIP_UPDATE_CHECK": "1",
        }
    )
    version = _run(
        [str(plan.cafe_executable), "version"],
        capture_output=True,
        cwd=plan.install_root,
        env=cli_env,
    ).stdout.strip()
    if not version.startswith("CAFE version "):
        raise BootstrapError(f"Unexpected version response from installed CAFE: {version}")
    sync_env = cli_env.copy()
    sync_env.pop("CAFE_SKIP_GLOBAL_SKILL_SYNC")
    _run(
        [str(plan.cafe_executable), "skill", "sync-global"],
        cwd=plan.install_root,
        env=sync_env,
    )
    return version.removeprefix("CAFE version ")


def _environment_python(environment: Path) -> Path:
    if os.name == "nt":
        return environment / "Scripts" / "python.exe"
    return environment / "bin" / "python"


def _publish_launcher(plan: BootstrapPlan) -> Optional[Path]:
    plan.bin_dir.mkdir(parents=True, exist_ok=True)
    temporary = plan.bin_dir / f".cafe-{uuid.uuid4().hex}.tmp"
    backup: Optional[Path] = None
    try:
        if os.name == "nt":
            content = (
                f"@{MANAGED_WINDOWS_LAUNCHER}\r\n"
                f'@"{plan.cafe_executable}" %*\r\n'
            )
            temporary.write_text(content, encoding="utf-8")
        else:
            temporary.symlink_to(plan.cafe_executable)
        if plan.launcher.exists() or plan.launcher.is_symlink():
            backup = plan.bin_dir / f".cafe-launcher-{uuid.uuid4().hex}.backup"
            plan.launcher.replace(backup)
        try:
            temporary.replace(plan.launcher)
        except Exception:
            if backup is not None:
                backup.replace(plan.launcher)
            raise
    finally:
        if temporary.exists() or temporary.is_symlink():
            temporary.unlink()
    return backup


def _rollback_launcher(plan: BootstrapPlan, backup: Optional[Path]) -> None:
    if plan.launcher.exists() or plan.launcher.is_symlink():
        plan.launcher.unlink()
    if backup is not None and (backup.exists() or backup.is_symlink()):
        backup.replace(plan.launcher)


def _remove_launcher_backup(backup: Optional[Path]) -> None:
    if backup is not None and (backup.exists() or backup.is_symlink()):
        backup.unlink()


def _manifest_path(plan: BootstrapPlan) -> Path:
    return plan.install_root / "install.json"


def _read_previous_environment(plan: BootstrapPlan) -> Optional[Path]:
    manifest = _manifest_path(plan)
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError, UnicodeError):
        return None
    value = data.get("environment") if isinstance(data, dict) else None
    return Path(value) if isinstance(value, str) else None


def _write_manifest(plan: BootstrapPlan, version: str) -> None:
    manifest = _manifest_path(plan)
    temporary = manifest.with_name(f".{manifest.name}.{uuid.uuid4().hex}.tmp")
    data = {
        "version": 1,
        "cafe_version": version,
        "environment": str(plan.environment),
        "launcher": str(plan.launcher),
        "source": str(plan.source),
    }
    try:
        temporary.write_text(
            json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        temporary.replace(manifest)
    finally:
        if temporary.exists():
            temporary.unlink()


def _remove_previous_environment(previous: Optional[Path], plan: BootstrapPlan) -> None:
    if previous is None or previous == plan.environment:
        return
    resolved = previous.expanduser().resolve()
    if resolved.parent != plan.environments_dir.resolve() or not resolved.name.startswith("env-"):
        return
    if resolved.is_dir() and not resolved.is_symlink():
        shutil.rmtree(resolved)


def _bin_dir_is_on_path(bin_dir: Path) -> bool:
    for entry in os.getenv("PATH", "").split(os.pathsep):
        if not entry:
            continue
        try:
            if Path(entry).expanduser().resolve() == bin_dir.resolve():
                return True
        except OSError:
            continue
    return False


def print_plan(plan: BootstrapPlan) -> None:
    """Print the complete user-scoped mutation plan."""
    print("CAFE user installation plan")
    print(f"  Source: {plan.source}")
    print(f"  Isolated environment: {plan.environment}")
    print(f"  Launcher: {plan.launcher}")
    print("  Global skills: detected Claude, Codex, Copilot, Cursor, and Gemini CLIs")
    print("  System packages and shell profiles will not be modified.")


def install(plan: BootstrapPlan) -> str:
    """Install, verify, sync skills, and publish the user launcher."""
    _validate_python()
    _validate_source(plan.source)
    _validate_launcher(plan)
    if plan.environment.exists() or plan.environment.is_symlink():
        raise BootstrapError(f"Generated environment already exists: {plan.environment}")

    previous = _read_previous_environment(plan)
    plan.environments_dir.mkdir(parents=True, exist_ok=True)
    launcher_backup: Optional[Path] = None
    try:
        version = _install_environment(plan)
        launcher_backup = _publish_launcher(plan)
        try:
            _write_manifest(plan, version)
        except Exception:
            _rollback_launcher(plan, launcher_backup)
            launcher_backup = None
            raise
    except Exception:
        if plan.environment.is_dir() and not plan.environment.is_symlink():
            shutil.rmtree(plan.environment)
        raise

    try:
        _remove_launcher_backup(launcher_backup)
    except OSError as exc:
        print(f"Warning: could not remove the launcher backup: {exc}", file=sys.stderr)
    try:
        _remove_previous_environment(previous, plan)
    except OSError as exc:
        print(f"Warning: could not remove the previous CAFE environment: {exc}", file=sys.stderr)
    return version


def _confirm() -> bool:
    if not sys.stdin.isatty():
        raise BootstrapError(
            "Non-interactive installation requires --yes after the user authorizes the plan"
        )
    return input("Continue with this user-scoped installation? [y/N] ").strip().lower() in {
        "y",
        "yes",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install CAFE and its workflow skills into user-scoped locations.",
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=Path.cwd(),
        help="Trusted CAFE source checkout (default: current directory)",
    )
    parser.add_argument("--install-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--bin-dir", type=Path, help=argparse.SUPPRESS)
    parser.add_argument(
        "--dry-run", action="store_true", help="Show the plan without writing files"
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Run non-interactively after the user has authorized the displayed plan",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    plan = create_plan(
        source=args.source,
        install_root=args.install_root,
        bin_dir=args.bin_dir,
    )
    try:
        _validate_python()
        _validate_source(plan.source)
        _validate_launcher(plan)
        print_plan(plan)
        if args.dry_run:
            return 0
        if not args.yes and not _confirm():
            print("Installation cancelled.")
            return 0
        version = install(plan)
    except (BootstrapError, OSError, subprocess.CalledProcessError) as exc:
        print(f"Installation failed: {exc}", file=sys.stderr)
        return 1

    print(f"Installed CAFE {version}.")
    print(f"Launcher: {plan.launcher}")
    if not _bin_dir_is_on_path(plan.bin_dir):
        print(
            f"Warning: {plan.bin_dir} is not on PATH. Use the launcher by its full path or "
            "ask before updating the shell profile.",
            file=sys.stderr,
        )
    print("Bundled workflow skills were synchronized for detected CLI agents.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
