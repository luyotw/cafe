"""Provider-owned reasoning admission and invocation projection.

Mechanisms and conservative model ranges verified 2026-10-11. See
``docs/reasoning-effort.md`` for primary sources and evidence limitations.
Unknown clients/models fail closed; omission never probes or changes defaults.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from functools import lru_cache, wraps
from contextvars import ContextVar
import inspect
from pathlib import Path
from typing import Mapping

from packaging.version import Version

_WORKSPACE = ContextVar("effort_workspace", default=None)


def scoped_controls(method):
    """Admit against the actual invocation workspace without changing host cwd."""
    signature = inspect.signature(method)

    @wraps(method)
    def scoped(*args, **kwargs):
        control = signature.bind(*args, **kwargs).arguments.get("execution_control")
        workspace = getattr(control, "working_directory", None)
        token = _WORKSPACE.set(
            Path(workspace).expanduser().resolve() if workspace is not None else _WORKSPACE.get()
        )
        try:
            return method(*args, **kwargs)
        finally:
            _WORKSPACE.reset(token)

    return scoped


PROJECTION_VERSION = 2
LEVELS = ("low", "medium", "high", "xhigh", "max")
CLAUDE_MODELS = {
    "claude-opus-4-6": ("low", "medium", "high", "max"),
    "claude-sonnet-4-6": ("low", "medium", "high", "max"),
    **{
        model: LEVELS
        for model in (
            "claude-opus-4-7",
            "claude-opus-4-8",
            "claude-opus-5",
            "claude-sonnet-5",
            "claude-opus-5-5",
            "claude-sonnet-5-5",
            "claude-haiku-5-5",
            "claude-fable-5",
            "claude-fable-5-1",
        )
    },
}
# A bounded subset verified in Copilot 1.0.83's production capability table.
COPILOT_MODELS = {
    "claude-opus-4.6": ("low", "medium", "high"),
    "claude-sonnet-4.6": ("low", "medium", "high"),
    "gpt-5.1": ("low", "medium", "high"),
    "gpt-5.2": ("low", "medium", "high"),
}


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    if not path.is_file() or path.stat().st_size > 4_194_304:
        raise ValueError("reasoning_effort: capability/control file must be bounded and readable")
    try:
        data = json.loads(path.read_text())
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(
            "reasoning_effort: capability/control file is unreadable; refresh provider metadata"
        ) from exc
    if not isinstance(data, dict):
        raise ValueError("reasoning_effort: invalid capability/control document")
    return data


@lru_cache(maxsize=32)
def _client_version(executable: str, size: int, mtime_ns: int) -> str:
    try:
        result = subprocess.run(
            [executable, "--version"], capture_output=True, text=True, timeout=5
        )
        match = re.search(r"\b\d+\.\d+\.\d+\b", result.stdout)
        if result.returncode == 0 and match:
            return match.group()
    except (OSError, subprocess.SubprocessError):
        pass
    raise ValueError(
        "reasoning_effort: cannot verify CLI version; install a supported client and retry preflight"
    )


def client_identity(cli: str) -> dict:
    executable = shutil.which(cli)
    if not executable:
        raise ValueError(
            f"reasoning_effort: CLI {cli} is unavailable; install it and retry preflight"
        )
    stat = Path(executable).stat()
    return {
        "executable": str(Path(executable).resolve()),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "version": _client_version(executable, stat.st_size, stat.st_mtime_ns),
    }


def supported_efforts(
    cli: str, model: str | None, environment: Mapping[str, str] | None = None
) -> tuple[str, ...]:
    environment = os.environ if environment is None else environment
    identity = client_identity(cli)
    version = Version(identity["version"])
    if cli == "codex" and version >= Version("0.159.3"):
        root = Path(environment.get("CODEX_HOME") or Path.home() / ".codex")
        cache = _read_json(root / "models_cache.json")
        # Capability entries remain usable across CLI upgrades; a different
        # cache client_version does not invalidate the model's supported levels.
        for entry in cache.get("models", []):
            if entry.get("slug") == model:
                return tuple(
                    level["effort"]
                    for level in entry.get("supported_reasoning_levels", [])
                    if isinstance(level, dict)
                    and re.fullmatch(r"[a-z]+", str(level.get("effort", "")))
                )
    elif cli == "claude" and version >= Version("2.1.289"):
        return CLAUDE_MODELS.get(model, ())
    elif cli == "copilot" and version >= Version("1.0.83"):
        return COPILOT_MODELS.get(model, ())
    return ()


def validate_effort(
    cli: str,
    model: str | None,
    effort: object,
    *,
    explicit: bool = False,
    environment: Mapping[str, str] | None = None,
) -> None:
    if effort is None and not explicit:
        return
    if type(effort) is not str or not re.fullmatch(r"[a-z]+", effort):
        raise ValueError(
            "reasoning_effort must be a non-empty supported lowercase token; omit the field to retain CLI defaults"
        )
    # Never include unvalidated control or model contents in diagnostics.
    safe_model = (
        model if isinstance(model, str) and re.fullmatch(r"[a-zA-Z0-9_.-]+", model) else "<unknown>"
    )
    if cli in {"cursor-agent", "gemini"}:
        raise ValueError(
            f"reasoning_effort: {cli}/{safe_model} has no verified independent invocation-scoped level control preserving managed settings; omit effort for legacy execution or select a verified adapter"
        )
    try:
        levels = supported_efforts(cli, model, environment)
    except ValueError as exc:
        raise ValueError(f"reasoning_effort: cannot admit {cli}/{safe_model}: {exc}") from exc
    if effort not in levels:
        raise ValueError(
            f'reasoning_effort: unsupported {cli}/{safe_model} combination; supported levels: {", ".join(levels) or "unknown"}; refresh client/model capability evidence or select a supported tuple'
        )


def control_paths(cli: str, environment: Mapping[str, str]) -> list[Path]:
    if cli == "codex":
        root = Path(environment.get("CODEX_HOME") or Path.home() / ".codex")
        return [
            root / "config.toml",
            root / "models_cache.json",
            Path("/etc/codex/requirements.toml"),
            (_WORKSPACE.get() or Path.cwd()) / ".codex/config.toml",
        ]
    if cli == "claude":
        root = Path(environment.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
        return [
            root / "settings.json",
            root / "managed-settings.json",
            Path("/etc/claude-code/managed-settings.json"),
            (_WORKSPACE.get() or Path.cwd()) / ".claude/settings.json",
            (_WORKSPACE.get() or Path.cwd()) / ".claude/settings.local.json",
        ]
    if cli == "copilot":
        return [Path(environment.get("COPILOT_HOME") or Path.home() / ".copilot") / "config.json"]
    return []


def validate_controls(
    cli: str, model: str | None, effort: str | None, environment: Mapping[str, str]
) -> None:
    if effort is None:
        return
    validate_effort(cli, model, effort, environment=environment)
    if cli == "claude":
        for path in control_paths(cli, environment):
            settings = _read_json(path)
            # Every applicable cap is enforced; project defaults cannot relax managed caps.
            cap = settings.get("maxEffortLevel")
            caps = [cap] if cap is not None else []
            model_settings = settings.get("modelSettings", {}).get(model, {})
            if isinstance(model_settings, dict) and "maxEffortLevel" in model_settings:
                caps.append(model_settings["maxEffortLevel"])
            for cap in caps:
                if cap not in LEVELS or LEVELS.index(effort) > LEVELS.index(cap):
                    raise ValueError(
                        "reasoning_effort conflicts with a Claude effort cap; choose an allowed tuple through existing confirmation"
                    )
        for key in ("CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING", "DISABLE_THINKING"):
            if environment.get(key) not in (None, "", "0", "false"):
                raise ValueError(
                    f"reasoning_effort conflicts with {key}; resolve the provider control before execution"
                )
    if cli == "copilot" and environment.get("COPILOT_ENABLE_ALT_PROVIDERS") == "true":
        raise ValueError("reasoning_effort: alternate Copilot provider capability is unverified")


def project_command(config, command: list[str]) -> list[str]:
    effort = config.reasoning_effort
    if effort is None:
        return command
    validate_controls(config.cli.value, config.model, effort, os.environ)
    if config.cli.value == "codex":
        # Global config placement works identically with exec and exec resume.
        return [command[0], "--config", f'model_reasoning_effort="{effort}"', *command[1:]]
    return [*command, "--effort", effort]


def project_environment(config, environment: Mapping[str, str]) -> dict[str, str]:
    result = dict(environment)
    validate_controls(config.cli.value, config.model, config.reasoning_effort, result)
    if config.reasoning_effort is not None and config.cli.value == "claude":
        result["CLAUDE_CODE_EFFORT_LEVEL"] = config.reasoning_effort
    return result


def capability_context(cli: str, model: str | None, effort: str | None) -> str:
    """Opaque cache identity; never persist raw user settings or secrets."""
    environment = os.environ
    adapter_name = {
        "codex": "codex",
        "claude": "claude",
        "copilot": "copilot",
        "gemini": "gemini",
        "cursor-agent": "cursor",
    }.get(cli)
    sources = [
        Path(__file__),
        Path(__file__).with_name("abstract.py"),
        Path(__file__).parents[1] / "executor.py",
    ]
    if adapter_name:
        sources.append(Path(__file__).with_name(adapter_name + ".py"))
    payload = {
        "projection": PROJECTION_VERSION,
        "workspace": str((_WORKSPACE.get() or Path.cwd()).resolve()),
        "adapter_sources": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sources
        },
        "client": client_identity(cli) if effort is not None else {"executable": shutil.which(cli)},
        "levels": supported_efforts(cli, model) if effort is not None else (),
        "environment": {
            key: value
            for key, value in environment.items()
            if key.startswith(("CODEX_", "CLAUDE_", "ANTHROPIC_", "COPILOT_", "GEMINI_"))
        },
        "controls": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in control_paths(cli, environment)
            if path.is_file()
        },
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def reasoning_evidence(
    config, command: list[str] | None = None, records=(), *, native_applied_effort=None
) -> dict:
    """Reconcile native reports without inferring a level from usage or exit."""
    configured = config.reasoning_effort
    applied = native_applied_effort
    if configured is not None and command is not None:
        if config.cli.value == "codex" and f'model_reasoning_effort="{configured}"' in command:
            applied = configured
        if config.cli.value in {"claude", "copilot"} and "--effort" in command:
            index = command.index("--effort")
            if index + 1 < len(command) and command[index + 1] == configured:
                applied = configured
    reported = None
    for raw in records:
        try:
            record = json.loads(raw) if isinstance(raw, str) else raw
        except (ValueError, TypeError):
            continue
        if not isinstance(record, dict):
            continue
        if record.get("type") not in {
            "system",
            "result",
            "turn.started",
            "turn.completed",
            "session.start",
            "session.info",
        }:
            continue
        for field in ("reasoning_effort", "effortLevel", "reasoningEffort"):
            value = record.get(field)
            if value is None:
                continue
            if not isinstance(value, str) or not re.fullmatch(r"[a-z]+", value):
                raise ValueError("reasoning_effort: invalid provider effort report")
            if reported is not None and reported != value:
                raise ValueError("reasoning_effort: conflicting provider effort reports")
            reported = value
    if configured is not None and reported is not None and reported != configured:
        error = ValueError("reasoning_effort: provider report differs from the configured tuple")
        error.reasoning_evidence = {
            "configured": configured,
            "applied": applied,
            "reported": reported,
            "verification": "mismatch",
            "provenance": "provider-stream",
        }
        raise error
    return {
        "configured": configured,
        "applied": applied,
        "reported": reported,
        "verification": (
            "provider-confirmed"
            if reported is not None
            else "invocation-derived" if applied is not None else "unknown/CLI-default"
        ),
        "provenance": (
            "provider-stream"
            if reported is not None
            else "validated-invocation" if applied is not None else "CLI-default"
        ),
    }
